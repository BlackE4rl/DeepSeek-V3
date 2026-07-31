"""Positions, the cash ledger and position sizing.

The ledger is single-currency: everything is converted to the account currency
at the moment of the fill and held as one cash balance. A real multi-currency
brokerage account keeps a balance per currency and charges a conversion only
when you ask for one, so this is a simplification -- it slightly overstates the
FX friction of holding foreign stocks and understates the FX risk carried in the
cash balance. It is stated in the report rather than hidden.

Position sizing is risk-based: the number of shares follows from the distance
between the entry and the initial stop, so a volatile symbol gets a smaller
position than a quiet one for the same money at risk.
"""

import math
from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from .config import PortfolioParams


@dataclass
class Position:
    """An open long position.

    Attributes:
        symbol: Ticker.
        currency: Currency the symbol trades in.
        shares: Number of shares held.
        entry_date: Date the position was filled.
        entry_price_local: Fill price in the symbol's own currency, slippage and
            spread already included.
        entry_reference_local: The untouched bar open the fill was derived from.
            Keeping both is what allows the gross and net figures to be
            reconciled without double counting the slippage, which is embedded
            in the fill price and must never also be subtracted from the P&L.
        entry_fx: Exchange rate at the fill.
        initial_stop_local: The stop set at entry, anchored to the fill.
        stop_local: The stop currently in force. Ratchets upwards only.
        atr_at_entry: ATR at the signal bar, used for the initial stop.
        highest_close: Highest close seen since entry.
        bars_held: Number of the symbol's own sessions since entry.
        entry_commission_acct: Commission paid on entry.
        entry_slippage_acct: Slippage and spread paid on entry.
        equity_at_signal: Account equity at the close the entry was decided on.
    """

    symbol: str
    currency: str
    shares: float
    entry_date: pd.Timestamp
    entry_price_local: float
    entry_reference_local: float
    entry_fx: float
    initial_stop_local: float
    stop_local: float
    atr_at_entry: float
    highest_close: float
    bars_held: int = 0
    entry_commission_acct: float = 0.0
    entry_slippage_acct: float = 0.0
    equity_at_signal: float = 0.0

    @property
    def initial_risk_acct(self) -> float:
        """Money at risk between the fill and the initial stop."""
        return self.shares * (self.entry_price_local - self.initial_stop_local) * self.entry_fx

    def current_risk_acct(self, mark_local: float, fx_rate: float) -> float:
        """Money still at risk at the current mark.

        Once the trailing stop has moved above the entry the position can no
        longer lose money against its plan, so its contribution to portfolio
        heat is zero rather than negative.

        Args:
            mark_local: Current price in the symbol's own currency.
            fx_rate: Current exchange rate.

        Returns:
            The open risk in account currency, never below zero.
        """
        return max(0.0, self.shares * (mark_local - self.stop_local) * fx_rate)


@dataclass
class Order:
    """A market order queued at the close of one bar for the next session.

    Attributes:
        symbol: Ticker.
        side: Either ``buy`` or ``sell``.
        shares: Intended size. Ignored for sells, which always close the position.
        signal_date: Date the order was decided on.
        execute_position: Position on the master index where it may fill, which
            is the symbol's next own session.
        expires_after: Last date the order may fill on.
        rank_score: Ranking score, used to order competing buys.
        reason: Why the order exists, carried into the trade record.
        atr_at_signal: ATR at the signal bar, used to place the initial stop.
        stop_distance_local: Planned distance between fill and initial stop.
        equity_at_signal: Account equity at the close the order was decided on.
            Carried into the trade record so that a trade's impact can be
            expressed as a fraction of the capital that was actually at work,
            which is what the bootstrap resamples.
        planned_risk_acct: Money the order is expected to put at risk, measured
            at the signal close. It is carried on the order so that the running
            portfolio heat can be decremented by exactly the amount that was
            added, rather than by a recomputed figure that would drift as the
            exchange rate moves between the signal and the fill.
    """

    symbol: str
    side: str
    shares: float
    signal_date: pd.Timestamp
    execute_position: int
    expires_after: pd.Timestamp
    rank_score: float = 0.0
    reason: str = ""
    atr_at_signal: float = 0.0
    stop_distance_local: float = 0.0
    equity_at_signal: float = 0.0
    planned_risk_acct: float = 0.0


@dataclass
class Trade:
    """A completed round trip."""

    symbol: str
    currency: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    shares: float
    entry_price_local: float
    exit_price_local: float
    fx_in: float
    fx_out: float
    gross_pnl_acct: float
    costs_acct: float
    net_pnl_acct: float
    r_multiple: float
    bars_held: int
    exit_reason: str
    local_return: float
    fx_return: float
    equity_at_signal: float
    pnl_fraction: float


@dataclass
class RejectedOrder:
    """An order that could not be placed or filled in full."""

    date: pd.Timestamp
    symbol: str
    side: str
    wanted_shares: float
    filled_shares: float
    reason: str


@dataclass
class SizingResult:
    """The outcome of the position sizing calculation.

    Attributes:
        shares: Share count after every cap and rounding.
        stop_distance_local: Distance between the planned entry and the stop.
        binding_constraint: Which cap determined the size, for the report.
    """

    shares: float
    stop_distance_local: float
    binding_constraint: str


class LedgerError(AssertionError):
    """Raised when the cash ledger stops adding up."""


class Portfolio:
    """Cash, open positions and the trade log.

    Attributes:
        params: Portfolio parameters.
        cash: Free cash in the account currency.
        positions: Open positions keyed by ticker.
        trades: Completed round trips.
        rejected: Orders that were shrunk or dropped.
    """

    def __init__(self, params: PortfolioParams) -> None:
        """Initialise an empty portfolio funded with the configured equity."""
        self.params = params
        self.cash = float(params.initial_equity)
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.rejected: List[RejectedOrder] = []
        self.total_costs_acct = 0.0
        self.total_commission_acct = 0.0
        self.total_slippage_acct = 0.0
        self.traded_notional_acct = 0.0
        self._cash_flows: List[float] = []

    # -- ledger ----------------------------------------------------------------

    def _apply_cash_flow(self, amount: float) -> None:
        """Move cash and remember the movement for the reconciliation check."""
        self.cash += amount
        self._cash_flows.append(amount)

    def check_ledger(self) -> None:
        """Verify that the cash balance equals the sum of every recorded flow.

        This is a genuine check rather than a tautology: ``cash`` is mutated
        only through :meth:`_apply_cash_flow`, so a fill that forgets to record
        its own commission, or records it twice, shows up here immediately.

        Raises:
            LedgerError: If the balance and the flows disagree, or cash is
                negative.
        """
        expected = self.params.initial_equity + sum(self._cash_flows)
        if abs(self.cash - expected) > 1e-6:
            raise LedgerError(
                f"cash {self.cash:.6f} does not match the sum of recorded flows "
                f"{expected:.6f}"
            )
        if self.cash < -1e-9:
            raise LedgerError(f"cash went negative: {self.cash:.6f}")

    # -- valuation -------------------------------------------------------------

    def positions_value(self, marks: pd.Series, rates: Dict[str, float]) -> float:
        """Value of all open positions in the account currency.

        Args:
            marks: Mark prices per ticker in local currency.
            rates: Exchange rate per currency.

        Returns:
            The summed position value.
        """
        total = 0.0
        for symbol, position in self.positions.items():
            total += position.shares * float(marks[symbol]) * rates[position.currency]
        return total

    def equity(self, marks: pd.Series, rates: Dict[str, float]) -> float:
        """Total account value: free cash plus the value of open positions."""
        return self.cash + self.positions_value(marks, rates)

    def open_risk(self, marks: pd.Series, rates: Dict[str, float]) -> float:
        """Summed money at risk across open positions, in account currency."""
        total = 0.0
        for symbol, position in self.positions.items():
            total += position.current_risk_acct(
                float(marks[symbol]), rates[position.currency]
            )
        return total

    def gross_exposure(self, marks: pd.Series, rates: Dict[str, float]) -> float:
        """Absolute exposure, which for a long-only book equals position value."""
        return self.positions_value(marks, rates)

    # -- trading ---------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        currency: str,
        shares: float,
        date: pd.Timestamp,
        price_local: float,
        reference_local: float,
        fx_rate: float,
        stop_local: float,
        atr_at_signal: float,
        commission_acct: float,
        equity_at_signal: float = 0.0,
    ) -> Position:
        """Record a purchase and pay for it.

        Args:
            symbol: Ticker.
            currency: Symbol's currency.
            shares: Shares bought.
            date: Fill date.
            price_local: Fill price including slippage and spread.
            reference_local: The bar's untouched open.
            fx_rate: Exchange rate at the fill.
            stop_local: Initial stop level.
            atr_at_signal: ATR at the signal bar.
            commission_acct: Commission paid.
            equity_at_signal: Account equity at the signal close.

        Returns:
            The newly opened Position.
        """
        notional_acct = shares * price_local * fx_rate
        slippage_acct = shares * (price_local - reference_local) * fx_rate

        self._apply_cash_flow(-(notional_acct + commission_acct))
        self.total_commission_acct += commission_acct
        self.total_slippage_acct += slippage_acct
        self.total_costs_acct += commission_acct + slippage_acct
        self.traded_notional_acct += notional_acct

        position = Position(
            symbol=symbol,
            currency=currency,
            shares=shares,
            entry_date=date,
            entry_price_local=price_local,
            entry_reference_local=reference_local,
            entry_fx=fx_rate,
            initial_stop_local=stop_local,
            stop_local=stop_local,
            atr_at_entry=atr_at_signal,
            highest_close=price_local,
            bars_held=0,
            entry_commission_acct=commission_acct,
            entry_slippage_acct=slippage_acct,
            equity_at_signal=equity_at_signal,
        )
        self.positions[symbol] = position
        return position

    def close_position(
        self,
        symbol: str,
        date: pd.Timestamp,
        price_local: float,
        reference_local: float,
        fx_rate: float,
        commission_acct: float,
        reason: str,
    ) -> Trade:
        """Record a sale and bank the proceeds.

        The gross figure is what the printed bar prices would have produced with
        no friction at all; the costs are commission plus the slippage embedded
        in both fill prices; the net figure is the actual change in cash. The
        three reconcile exactly, which is what the cost tests check.

        Args:
            symbol: Ticker.
            date: Fill date.
            price_local: Fill price after slippage and spread.
            reference_local: The untouched reference price the fill came from.
            fx_rate: Exchange rate at the fill.
            commission_acct: Commission paid.
            reason: Why the position was closed.

        Returns:
            The completed Trade.
        """
        position = self.positions.pop(symbol)
        proceeds_acct = position.shares * price_local * fx_rate
        slippage_acct = position.shares * (reference_local - price_local) * fx_rate

        self._apply_cash_flow(proceeds_acct - commission_acct)
        self.total_commission_acct += commission_acct
        self.total_slippage_acct += slippage_acct
        self.total_costs_acct += commission_acct + slippage_acct
        self.traded_notional_acct += proceeds_acct

        gross_pnl = position.shares * (
            reference_local * fx_rate - position.entry_reference_local * position.entry_fx
        )
        costs = (
            position.entry_commission_acct
            + position.entry_slippage_acct
            + commission_acct
            + slippage_acct
        )
        net_pnl = gross_pnl - costs

        risk = position.initial_risk_acct
        r_multiple = net_pnl / risk if risk > 0 else float("nan")

        trade = Trade(
            symbol=symbol,
            currency=position.currency,
            entry_date=position.entry_date,
            exit_date=date,
            shares=position.shares,
            entry_price_local=position.entry_price_local,
            exit_price_local=price_local,
            fx_in=position.entry_fx,
            fx_out=fx_rate,
            gross_pnl_acct=gross_pnl,
            costs_acct=costs,
            net_pnl_acct=net_pnl,
            r_multiple=r_multiple,
            bars_held=position.bars_held,
            exit_reason=reason,
            local_return=price_local / position.entry_price_local - 1.0,
            fx_return=fx_rate / position.entry_fx - 1.0,
            equity_at_signal=position.equity_at_signal,
            pnl_fraction=(
                net_pnl / position.equity_at_signal
                if position.equity_at_signal > 0
                else float("nan")
            ),
        )
        self.trades.append(trade)
        return trade

    def reject(
        self,
        date: pd.Timestamp,
        symbol: str,
        side: str,
        wanted: float,
        filled: float,
        reason: str,
    ) -> None:
        """Log an order that could not be placed or filled in full."""
        self.rejected.append(
            RejectedOrder(
                date=date,
                symbol=symbol,
                side=side,
                wanted_shares=wanted,
                filled_shares=filled,
                reason=reason,
            )
        )


def size_position(
    equity_acct: float,
    close_local: float,
    atr_local: float,
    adv_shares: float,
    fx_rate: float,
    stop_multiple: float,
    params: PortfolioParams,
) -> SizingResult:
    """Compute the share count for a new position.

    Three caps apply and the smallest wins: the risk budget, the notional weight
    limit, and the share of the symbol's average volume the order may represent.

    The sizing uses the signal bar's close while the order will fill at the next
    open, so the realized risk deviates a little from the target. That is not a
    bug to be corrected -- the fill price is genuinely unknown at the moment the
    decision is made, and pretending otherwise would be look-ahead.

    Args:
        equity_acct: Account equity at the signal close.
        close_local: Signal bar close in the symbol's own currency.
        atr_local: ATR at the signal bar.
        adv_shares: 20-day average share volume.
        fx_rate: Account currency per unit of the symbol's currency.
        stop_multiple: ATR multiple of the initial stop.
        params: Portfolio parameters.

    Returns:
        The SizingResult, whose ``shares`` may be zero when no cap allows a
        position at all.
    """
    stop_distance = stop_multiple * atr_local
    if not (stop_distance > 0) or not (close_local > 0) or not (fx_rate > 0):
        return SizingResult(0.0, 0.0, "invalid inputs")

    risk_acct = equity_acct * params.risk_per_trade
    by_risk = risk_acct / (stop_distance * fx_rate)
    by_notional = (equity_acct * params.max_position_weight) / (close_local * fx_rate)
    by_liquidity = params.max_adv_participation * adv_shares if adv_shares > 0 else 0.0

    caps = {
        "risk": by_risk,
        "notional weight": by_notional,
        "liquidity": by_liquidity,
    }
    binding = min(caps, key=caps.get)
    raw = caps[binding]

    shares = raw if params.allow_fractional_shares else math.floor(raw)
    return SizingResult(float(max(shares, 0.0)), stop_distance, binding)
