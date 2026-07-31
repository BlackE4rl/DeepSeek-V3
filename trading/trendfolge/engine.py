"""The backtest engine.

The loop walks the master date index one bar at a time and, on each bar, does
four things in this fixed order:

1. fill the orders that were queued at an earlier close and whose execution
   session is today -- sells first, because they free cash, then buys in
   ranking order;
2. sweep the open positions for stop executions, using the stop level that was
   fixed at the close of each position's *previous* session;
3. mark the book to market and record the equity;
4. run the close-of-day pass: advance trailing stops, queue exits, evaluate the
   entry rules and queue tomorrow's buys.

Why event-driven rather than vectorized
---------------------------------------
A vectorized ``signal.shift(1) * returns`` pipeline is faster to write and
produces a beautiful, fictitious equity curve. It silently assumes unlimited
capital, no position limit, no ranking when more signals fire than there are
slots, and no cash constraint. It also cannot express a trailing stop, which is
path dependent, nor an intraday stop fill, which needs the bar's low and a gap
rule. Every one of those omissions flatters the result.

The cost of doing it properly is irrelevant at this scale: roughly twenty-five
symbols over fifteen years is on the order of a hundred thousand bar-symbol
events, which runs in about a second of plain Python.
"""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel, build_cost_model
from .datasets.panel import Panel, describe_panel
from .portfolio import Order, Portfolio, size_position
from .strategy import initial_stop, trailing_stop
from .universe import Universe

EQUITY_COLUMNS = [
    "equity",
    "cash",
    "positions_value",
    "n_positions",
    "open_risk",
    "regime",
]


class EngineError(RuntimeError):
    """Raised when the simulation reaches a state that should be impossible."""


@dataclass
class BacktestResult:
    """Everything a run produces.

    Attributes:
        equity: Daily account state, indexed by date.
        trades: One row per completed round trip.
        rejected: Orders that were shrunk or dropped, with the reason.
        open_positions: Positions still open at the end of the run.
        config: The configuration used.
        config_hash: Hash of that configuration, for reproducibility.
        universe_name: Name of the universe.
        account_currency: Currency everything is reported in.
        cost_totals: Commission, slippage and turnover totals.
        data_notes: Provenance and data quality warnings for the report header.
    """

    equity: pd.DataFrame
    trades: pd.DataFrame
    rejected: pd.DataFrame
    open_positions: pd.DataFrame
    config: Config
    config_hash: str
    universe_name: str
    account_currency: str
    cost_totals: Dict[str, float] = field(default_factory=dict)
    data_notes: List[str] = field(default_factory=list)

    @property
    def final_equity(self) -> float:
        """Account value on the last bar."""
        return float(self.equity["equity"].iloc[-1])


def run_backtest(
    panel: Panel,
    config: Config,
    universe: Universe,
    cost_model: Optional[CostModel] = None,
    initial_equity: Optional[float] = None,
) -> BacktestResult:
    """Simulate the strategy over a panel.

    Args:
        panel: Aligned market data.
        config: Resolved configuration.
        universe: The universe definition, used for per-symbol spread overrides.
        cost_model: Cost model to use. Built from the config when omitted.
        initial_equity: Starting capital, overriding the configured value. The
            walk-forward harness uses this to carry equity across windows.

    Returns:
        The BacktestResult.
    """
    costs = cost_model or build_cost_model(config.costs, universe)
    portfolio_params = config.portfolio
    if initial_equity is not None:
        portfolio_params = _replace_equity(portfolio_params, initial_equity)
    portfolio = Portfolio(portfolio_params)

    strategy_params = config.strategy
    index = panel.master_index
    n_bars = len(index)
    symbols = list(panel.tradable)

    arrays = _PanelArrays(panel, symbols)
    pending: Dict[str, Order] = {}
    pending_risk_acct = 0.0
    cooldown_until: Dict[str, int] = {}

    equity_rows = np.empty((n_bars, len(EQUITY_COLUMNS)), dtype=float)

    for position in range(n_bars):
        date = index[position]

        # ---- 1. fill orders queued earlier ---------------------------------
        due = [order for order in pending.values() if order.execute_position == position]
        due.sort(key=lambda order: (order.side != "sell", -order.rank_score, order.symbol))
        for order in due:
            del pending[order.symbol]
            pending_risk_acct -= order.planned_risk_acct
            _execute_order(order, position, date, panel, arrays, portfolio, costs, strategy_params)
        pending_risk_acct = max(pending_risk_acct, 0.0)

        # ---- 2. stop sweep --------------------------------------------------
        for symbol in list(portfolio.positions):
            if not arrays.has_session(symbol, position):
                continue
            open_position = portfolio.positions[symbol]
            low = arrays.low[symbol][position]
            if low <= open_position.stop_local:
                bar_open = arrays.open[symbol][position]
                reference = min(bar_open, open_position.stop_local)
                execution = costs.sell(symbol, reference, is_stop=True)
                rate = arrays.rate(open_position.currency, position, date)
                commission = costs.commission(
                    open_position.shares * execution.price_local * rate
                )
                portfolio.close_position(
                    symbol,
                    date,
                    execution.price_local,
                    execution.reference_price_local,
                    rate,
                    commission,
                    "stop",
                )
                cooldown_until[symbol] = _advance_sessions(
                    panel, symbol, position, strategy_params.cooldown_bars
                )
                pending.pop(symbol, None)

        # ---- 3. mark to market ---------------------------------------------
        rates_now = {
            currency: arrays.rate(currency, position, date)
            for currency in arrays.currencies_of(portfolio.positions)
        }
        marks = arrays.marks_at(position)
        positions_value = portfolio.positions_value(marks, rates_now)
        equity = portfolio.cash + positions_value
        open_risk = portfolio.open_risk(marks, rates_now)
        regime_on = bool(arrays.regime[position])

        equity_rows[position] = (
            equity,
            portfolio.cash,
            positions_value,
            len(portfolio.positions),
            open_risk,
            float(regime_on),
        )

        if config.backtest.debug_assertions:
            portfolio.check_ledger()
            if len(portfolio.positions) > portfolio_params.max_positions:
                raise EngineError(
                    f"{date.date()}: {len(portfolio.positions)} positions open, "
                    f"limit is {portfolio_params.max_positions}"
                )

        if position == n_bars - 1:
            break

        # ---- 4. close-of-day pass ------------------------------------------
        _advance_stops(portfolio, arrays, position, strategy_params)
        _queue_exits(
            portfolio, pending, panel, arrays, position, date, strategy_params, regime_on
        )
        pending_risk_acct = _queue_entries(
            portfolio,
            pending,
            pending_risk_acct,
            panel,
            arrays,
            position,
            date,
            config,
            portfolio_params,
            equity,
            open_risk,
            regime_on,
            cooldown_until,
            symbols,
        )

    equity_frame = pd.DataFrame(equity_rows, index=index, columns=EQUITY_COLUMNS)
    equity_frame["regime"] = equity_frame["regime"].astype(bool)

    return BacktestResult(
        equity=equity_frame,
        trades=_trades_frame(portfolio),
        rejected=_rejected_frame(portfolio),
        open_positions=_open_positions_frame(portfolio),
        config=config,
        config_hash=config.hash,
        universe_name=universe.name,
        account_currency=universe.account_currency,
        cost_totals={
            "commission": portfolio.total_commission_acct,
            "slippage": portfolio.total_slippage_acct,
            "total": portfolio.total_costs_acct,
            "traded_notional": portfolio.traded_notional_acct,
        },
        data_notes=describe_panel(panel),
    )


# ---------------------------------------------------------------------------
# bar-level helpers
# ---------------------------------------------------------------------------


def _execute_order(
    order: Order,
    position: int,
    date: pd.Timestamp,
    panel: Panel,
    arrays: "_PanelArrays",
    portfolio: Portfolio,
    costs: CostModel,
    strategy_params,
) -> None:
    """Fill one order at the open of the current bar."""
    symbol = order.symbol
    if not arrays.has_session(symbol, position):
        portfolio.reject(date, symbol, order.side, order.shares, 0.0, "no session")
        return

    bar_open = arrays.open[symbol][position]
    currency = panel.currency(symbol)
    rate = arrays.rate(currency, position, date)

    if order.side == "sell":
        if symbol not in portfolio.positions:
            return
        held = portfolio.positions[symbol]
        execution = costs.sell(symbol, bar_open, is_stop=False)
        commission = costs.commission(held.shares * execution.price_local * rate)
        portfolio.close_position(
            symbol,
            date,
            execution.price_local,
            execution.reference_price_local,
            rate,
            commission,
            order.reason,
        )
        return

    if symbol in portfolio.positions:
        portfolio.reject(date, symbol, "buy", order.shares, 0.0, "already held")
        return

    execution = costs.buy(symbol, bar_open)
    shares = order.shares
    per_share_acct = execution.price_local * rate
    if per_share_acct <= 0:
        portfolio.reject(date, symbol, "buy", order.shares, 0.0, "invalid price")
        return

    # Re-check cash against the price actually available, which was unknown when
    # the order was sized at yesterday's close.
    affordable = portfolio.cash - costs.commission(shares * per_share_acct)
    if shares * per_share_acct > affordable:
        shares = float(np.floor(max(affordable, 0.0) / per_share_acct))
        if shares <= 0:
            portfolio.reject(date, symbol, "buy", order.shares, 0.0, "insufficient cash")
            return
        portfolio.reject(date, symbol, "buy", order.shares, shares, "cash truncated")

    commission = costs.commission(shares * per_share_acct)
    if shares * per_share_acct + commission > portfolio.cash + 1e-9:
        portfolio.reject(date, symbol, "buy", order.shares, 0.0, "insufficient cash")
        return

    stop = initial_stop(execution.price_local, order.atr_at_signal, strategy_params)
    portfolio.open_position(
        symbol=symbol,
        currency=currency,
        shares=shares,
        date=date,
        price_local=execution.price_local,
        reference_local=execution.reference_price_local,
        fx_rate=rate,
        stop_local=stop,
        atr_at_signal=order.atr_at_signal,
        commission_acct=commission,
        equity_at_signal=order.equity_at_signal,
    )


def _advance_stops(
    portfolio: Portfolio, arrays: "_PanelArrays", position: int, strategy_params
) -> None:
    """Update trailing stops and bar counts at the close of the current bar."""
    for symbol, held in portfolio.positions.items():
        if not arrays.has_session(symbol, position):
            continue
        close = arrays.close[symbol][position]
        held.bars_held += 1
        held.highest_close = max(held.highest_close, close)
        held.stop_local = trailing_stop(
            held.stop_local,
            held.highest_close,
            arrays.atr[symbol][position],
            strategy_params,
        )


def _queue_exits(
    portfolio: Portfolio,
    pending: Dict[str, Order],
    panel: Panel,
    arrays: "_PanelArrays",
    position: int,
    date: pd.Timestamp,
    strategy_params,
    regime_on: bool,
) -> None:
    """Queue market-on-open exits decided at this close."""
    for symbol, held in portfolio.positions.items():
        if symbol in pending or not arrays.has_session(symbol, position):
            continue

        reason = ""
        if arrays.below_long[symbol][position]:
            reason = "trend break"
        elif strategy_params.time_stop_bars and held.bars_held >= strategy_params.time_stop_bars:
            reason = "time stop"
        elif strategy_params.exit_on_regime_off and not regime_on:
            reason = "regime off"
        if not reason:
            continue

        execute_at = panel.next_session_position(symbol, position)
        if execute_at < 0:
            continue
        pending[symbol] = Order(
            symbol=symbol,
            side="sell",
            shares=held.shares,
            signal_date=date,
            execute_position=execute_at,
            expires_after=date + pd.Timedelta(days=365),
            reason=reason,
        )


def _queue_entries(
    portfolio: Portfolio,
    pending: Dict[str, Order],
    pending_risk_acct: float,
    panel: Panel,
    arrays: "_PanelArrays",
    position: int,
    date: pd.Timestamp,
    config: Config,
    portfolio_params,
    equity: float,
    open_risk: float,
    regime_on: bool,
    cooldown_until: Dict[str, int],
    symbols: List[str],
) -> float:
    """Evaluate the entry rules and queue tomorrow's buys.

    Returns:
        The updated planned risk of all pending buy orders.
    """
    if not regime_on:
        return pending_risk_acct

    pending_buys = sum(1 for order in pending.values() if order.side == "buy")
    pending_sells = sum(1 for order in pending.values() if order.side == "sell")
    projected = len(portfolio.positions) + pending_buys - pending_sells
    free_slots = portfolio_params.max_positions - projected
    if free_slots <= 0:
        return pending_risk_acct

    candidates = []
    for column, symbol in enumerate(symbols):
        if not arrays.entry_raw[position, column]:
            continue
        if symbol in portfolio.positions or symbol in pending:
            continue
        if position < cooldown_until.get(symbol, -1):
            continue
        score = arrays.rank_score[symbol][position]
        if not np.isfinite(score):
            continue
        candidates.append((-score, symbol))
    if not candidates:
        return pending_risk_acct

    candidates.sort()
    heat_budget = equity * portfolio_params.max_portfolio_heat

    for negative_score, symbol in candidates:
        if free_slots <= 0:
            break
        currency = panel.currency(symbol)
        try:
            rate = arrays.rate(currency, position, date)
        except Exception:  # pragma: no cover - covered by the FX tests
            continue

        sizing = size_position(
            equity_acct=equity,
            close_local=arrays.close[symbol][position],
            atr_local=arrays.atr[symbol][position],
            adv_shares=arrays.adv_shares[symbol][position],
            fx_rate=rate,
            stop_multiple=config.strategy.atr_stop_mult,
            params=portfolio_params,
        )
        if sizing.shares <= 0:
            portfolio.reject(date, symbol, "buy", 0.0, 0.0, f"size 0 ({sizing.binding_constraint})")
            continue

        planned_risk = sizing.shares * sizing.stop_distance_local * rate
        headroom = heat_budget - open_risk - pending_risk_acct
        if headroom <= 0:
            portfolio.reject(date, symbol, "buy", sizing.shares, 0.0, "portfolio heat")
            continue
        if planned_risk > headroom:
            scaled = np.floor(sizing.shares * headroom / planned_risk)
            if scaled <= 0:
                portfolio.reject(date, symbol, "buy", sizing.shares, 0.0, "portfolio heat")
                continue
            portfolio.reject(date, symbol, "buy", sizing.shares, scaled, "heat truncated")
            sizing.shares = float(scaled)
            planned_risk = sizing.shares * sizing.stop_distance_local * rate

        execute_at = panel.next_session_position(symbol, position)
        if execute_at < 0:
            continue
        expires_after = date + pd.Timedelta(days=portfolio_params.max_order_age_days)
        if panel.master_index[execute_at] > expires_after:
            portfolio.reject(date, symbol, "buy", sizing.shares, 0.0, "order expired")
            continue

        pending[symbol] = Order(
            symbol=symbol,
            side="buy",
            shares=sizing.shares,
            signal_date=date,
            execute_position=execute_at,
            expires_after=expires_after,
            rank_score=-negative_score,
            reason="pullback entry",
            atr_at_signal=arrays.atr[symbol][position],
            stop_distance_local=sizing.stop_distance_local,
            equity_at_signal=equity,
            planned_risk_acct=planned_risk,
        )
        pending_risk_acct += planned_risk
        free_slots -= 1

    return pending_risk_acct


def _advance_sessions(panel: Panel, symbol: str, position: int, count: int) -> int:
    """Position ``count`` of the symbol's own sessions after ``position``.

    Args:
        panel: The panel.
        symbol: Ticker.
        position: Starting position on the master index.
        count: Number of the symbol's sessions to skip.

    Returns:
        The resulting position, or one past the end when the symbol runs out of
        sessions, which blocks re-entry for the remainder of the run.
    """
    current = position
    for _ in range(max(count, 0)):
        current = panel.next_session_position(symbol, current)
        if current < 0:
            return len(panel.master_index)
    return current


def _replace_equity(params, equity: float):
    """Return a copy of the portfolio parameters with a different starting equity."""
    from dataclasses import replace

    return replace(params, initial_equity=float(equity))


# ---------------------------------------------------------------------------
# result assembly
# ---------------------------------------------------------------------------


def _trades_frame(portfolio: Portfolio) -> pd.DataFrame:
    """Convert the trade log to a DataFrame."""
    if not portfolio.trades:
        return pd.DataFrame(
            columns=[
                "symbol", "currency", "entry_date", "exit_date", "shares",
                "entry_price_local", "exit_price_local", "fx_in", "fx_out",
                "gross_pnl_acct", "costs_acct", "net_pnl_acct", "r_multiple",
                "bars_held", "exit_reason", "local_return", "fx_return",
                "equity_at_signal", "pnl_fraction",
            ]
        )
    return pd.DataFrame([asdict(trade) for trade in portfolio.trades])


def _rejected_frame(portfolio: Portfolio) -> pd.DataFrame:
    """Convert the rejected-order log to a DataFrame."""
    if not portfolio.rejected:
        return pd.DataFrame(
            columns=["date", "symbol", "side", "wanted_shares", "filled_shares", "reason"]
        )
    return pd.DataFrame([asdict(entry) for entry in portfolio.rejected])


def _open_positions_frame(portfolio: Portfolio) -> pd.DataFrame:
    """Convert the still-open positions to a DataFrame."""
    if not portfolio.positions:
        return pd.DataFrame(
            columns=["symbol", "currency", "shares", "entry_date", "entry_price_local",
                     "entry_fx", "entry_commission_acct", "stop_local", "bars_held"]
        )
    return pd.DataFrame(
        [
            {
                "symbol": held.symbol,
                "currency": held.currency,
                "shares": held.shares,
                "entry_date": held.entry_date,
                "entry_price_local": held.entry_price_local,
                "entry_fx": held.entry_fx,
                "entry_commission_acct": held.entry_commission_acct,
                "stop_local": held.stop_local,
                "bars_held": held.bars_held,
            }
            for held in portfolio.positions.values()
        ]
    )


class _PanelArrays:
    """Numpy views of the panel, so the inner loop does no pandas lookups."""

    def __init__(self, panel: Panel, symbols: List[str]) -> None:
        """Extract the arrays the loop needs.

        Args:
            panel: The panel.
            symbols: Tradable tickers, in the column order used for ranking.
        """
        self.panel = panel
        self.symbols = symbols
        self.index = panel.master_index

        self.open = {s: panel.px_open[s].to_numpy(dtype=float) for s in symbols}
        self.high = {s: panel.px_high[s].to_numpy(dtype=float) for s in symbols}
        self.low = {s: panel.px_low[s].to_numpy(dtype=float) for s in symbols}
        self.close = {s: panel.px_close[s].to_numpy(dtype=float) for s in symbols}
        self.mark = {s: panel.px_mark[s].to_numpy(dtype=float) for s in symbols}
        self._sessions = {s: panel.sessions[s].to_numpy(dtype=bool) for s in symbols}

        self.atr = {s: panel.signals[s]["atr"].to_numpy(dtype=float) for s in symbols}
        self.adv_shares = {
            s: panel.signals[s]["adv_shares"].to_numpy(dtype=float) for s in symbols
        }
        self.rank_score = {
            s: panel.signals[s]["rank_score"].to_numpy(dtype=float) for s in symbols
        }
        self.below_long = {
            s: panel.signals[s]["below_long"].to_numpy(dtype=bool) for s in symbols
        }
        self.entry_raw = (
            np.column_stack(
                [panel.signals[s]["entry_raw"].to_numpy(dtype=bool) for s in symbols]
            )
            if symbols
            else np.zeros((len(self.index), 0), dtype=bool)
        )
        self.regime = panel.regime.to_numpy(dtype=bool)

        fx_frame = panel.fx.frame
        self._rates = {
            currency: fx_frame[currency].to_numpy(dtype=float)
            for currency in fx_frame.columns
        }
        self._account_currency = panel.fx.account_currency

    def has_session(self, symbol: str, position: int) -> bool:
        """Whether the symbol traded on the bar at ``position``."""
        return bool(self._sessions[symbol][position])

    def rate(self, currency: str, position: int, date: pd.Timestamp) -> float:
        """Exchange rate for a currency on a bar.

        Raises:
            EngineError: If no rate is known, which means the FX series does not
                cover the backtest range.
        """
        if currency == self._account_currency:
            return 1.0
        series = self._rates.get(currency)
        if series is None:
            raise EngineError(f"no exchange rate loaded for {currency}")
        value = series[position]
        if not np.isfinite(value):
            raise EngineError(
                f"no {currency}/{self._account_currency} rate on {date.date()}"
            )
        return float(value)

    def currencies_of(self, positions) -> List[str]:
        """Distinct currencies among the open positions."""
        return list({held.currency for held in positions.values()})

    def marks_at(self, position: int) -> pd.Series:
        """Mark prices for every tradable symbol on one bar."""
        return pd.Series(
            {symbol: self.mark[symbol][position] for symbol in self.symbols}
        )
