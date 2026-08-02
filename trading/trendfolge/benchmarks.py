"""Benchmarks the strategy has to beat.

Two are computed, both in the account currency and both after the same
transaction costs the strategy pays:

* buy and hold of the benchmark instrument, bought once at the start;
* an equally weighted basket of the tradable universe, rebalanced monthly.

Charging the benchmarks the same costs matters. A frictionless benchmark makes
any strategy look better than it is, and the equal-weight basket in particular
would otherwise get free monthly rebalancing that no real investor receives.

The comparison against buy and hold is the headline of every report for a
reason. Trend-following mega-cap technology over the last fifteen years mostly
measures the fact that mega-cap technology went up a great deal. The only
question worth answering is whether the rules added anything on top of simply
owning the index -- after costs, and out of sample.
"""

from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from .costs import CostModel
from .datasets.panel import Panel
from .metrics import daily_returns


def buy_and_hold(
    panel: Panel,
    symbol: str,
    initial_equity: float,
    costs: CostModel,
    currency: Optional[str] = None,
) -> pd.Series:
    """Equity curve of buying one instrument on the first bar and holding it.

    Args:
        panel: The panel.
        symbol: Instrument to hold.
        initial_equity: Starting capital in the account currency.
        costs: Cost model, applied to the single purchase.
        currency: The instrument's currency. Taken from the panel when omitted.

    Returns:
        The equity curve, indexed by the master index.
    """
    index = panel.master_index
    if symbol not in panel.px_open.columns:
        return pd.Series(np.nan, index=index, name=f"{symbol} buy & hold")

    currency = currency or panel.meta[symbol].currency
    opens = panel.px_open[symbol]
    first_valid = opens.first_valid_index()
    if first_valid is None:
        return pd.Series(np.nan, index=index, name=f"{symbol} buy & hold")

    entry = costs.buy(symbol, float(opens.loc[first_valid]))
    rate_in = panel.fx.rate(currency, first_valid)
    per_share = entry.price_local * rate_in
    shares = np.floor((initial_equity - costs.commission(initial_equity)) / per_share)
    cash = initial_equity - shares * per_share - costs.commission(shares * per_share)

    rates = _rate_series(panel, currency)
    value = shares * panel.px_mark[symbol] * rates + cash
    value.loc[index < first_valid] = initial_equity
    return value.ffill().rename(f"{symbol} buy & hold")


def equal_weight_universe(
    panel: Panel,
    initial_equity: float,
    costs: CostModel,
    rebalance: str = "ME",
) -> pd.Series:
    """Equity curve of an equally weighted, periodically rebalanced basket.

    Only symbols that have already started trading are included, so the basket
    grows as the universe comes to life rather than assuming perfect foresight
    about which companies would be listed.

    Args:
        panel: The panel.
        initial_equity: Starting capital in the account currency.
        costs: Cost model, charged on every rebalancing trade.
        rebalance: Pandas offset alias for the rebalancing frequency.

    Returns:
        The equity curve, indexed by the master index.
    """
    index = panel.master_index
    symbols = list(panel.tradable)
    if not symbols:
        return pd.Series(initial_equity, index=index, name="equal weight")

    rebalance_dates = set(
        pd.Series(index, index=index).resample(rebalance).last().dropna()
    )

    rates = {symbol: _rate_series(panel, panel.meta[symbol].currency) for symbol in symbols}
    marks = {symbol: panel.px_mark[symbol] for symbol in symbols}
    opens = {symbol: panel.px_open[symbol] for symbol in symbols}
    sessions = {symbol: panel.sessions[symbol] for symbol in symbols}

    cash = float(initial_equity)
    shares: Dict[str, float] = {symbol: 0.0 for symbol in symbols}
    values: List[float] = []
    pending_rebalance = True

    for date in index:
        if pending_rebalance:
            tradable_now = [
                symbol
                for symbol in symbols
                if bool(sessions[symbol].loc[date]) and np.isfinite(opens[symbol].loc[date])
            ]
            if tradable_now:
                equity_now = cash + sum(
                    shares[symbol] * _mark(marks[symbol], date) * _mark(rates[symbol], date)
                    for symbol in symbols
                )
                target_value = equity_now / len(tradable_now)
                for symbol in symbols:
                    if symbol not in tradable_now:
                        continue
                    rate = float(rates[symbol].loc[date])
                    reference = float(opens[symbol].loc[date])
                    target_shares = np.floor(target_value / (reference * rate))
                    delta = target_shares - shares[symbol]
                    if delta == 0:
                        continue
                    if delta > 0:
                        execution = costs.buy(symbol, reference)
                        notional = delta * execution.price_local * rate
                        commission = costs.commission(notional)
                        if notional + commission > cash:
                            continue
                        cash -= notional + commission
                    else:
                        execution = costs.sell(symbol, reference)
                        notional = -delta * execution.price_local * rate
                        cash += notional - costs.commission(notional)
                    shares[symbol] = target_shares
                pending_rebalance = False

        total = cash + sum(
            shares[symbol] * _mark(marks[symbol], date) * _mark(rates[symbol], date)
            for symbol in symbols
        )
        values.append(total)

        if date in rebalance_dates:
            pending_rebalance = True

    return pd.Series(values, index=index, name="equal weight")


def exposure_matched(
    benchmark_equity: pd.Series,
    target_weight: float,
    initial_equity: float,
    risk_free_rate: float = 0.0,
) -> pd.Series:
    """The benchmark held at a fixed weight, with the remainder in cash.

    A trend-following strategy spends much of its life flat. Comparing the
    growth rate of a book that is invested a third of the time against an ETF
    that is invested all of the time mostly measures the difference in exposure,
    not skill. This curve puts the same *average* fraction of capital into the
    benchmark and leaves the rest earning the risk-free rate, so the two can be
    read side by side.

    The weighting is applied to the return series rather than by simulating
    rebalancing trades, so no rebalancing cost is charged. That favours this
    benchmark slightly, which is the conservative direction: the strategy has to
    beat a version of the alternative that is a little better than reality.

    Args:
        benchmark_equity: The benchmark's equity curve.
        target_weight: Fraction of capital to hold in it, typically the
            strategy's average invested fraction.
        initial_equity: Starting capital.
        risk_free_rate: Annualized rate earned on the uninvested remainder.

    Returns:
        The blended equity curve, on the benchmark's index.
    """
    clean = benchmark_equity.dropna()
    if clean.empty:
        return pd.Series(dtype=float, name="exposure matched")

    weight = float(np.clip(target_weight, 0.0, 1.0))
    blended = weight * clean.pct_change().fillna(0.0) + (1.0 - weight) * (
        risk_free_rate / 252.0
    )
    blended.iloc[0] = 0.0
    curve = initial_equity * (1.0 + blended).cumprod()
    return curve.reindex(benchmark_equity.index).rename(
        f"{benchmark_equity.name or 'benchmark'} at {weight:.0%} exposure"
    )


def relative_statistics(
    strategy_equity: pd.Series, benchmark_equity: pd.Series
) -> Dict[str, float]:
    """Beta, alpha, correlation and capture ratios against a benchmark.

    Args:
        strategy_equity: The strategy's equity curve.
        benchmark_equity: The benchmark's equity curve on the same index.

    Returns:
        A dictionary of relative statistics, NaN where undefined.
    """
    strategy = daily_returns(strategy_equity)
    benchmark = daily_returns(benchmark_equity)
    joined = pd.concat([strategy, benchmark], axis=1, keys=["strategy", "benchmark"]).dropna()
    if len(joined) < 30:
        return {
            "beta": float("nan"),
            "alpha_annual": float("nan"),
            "correlation": float("nan"),
            "up_capture": float("nan"),
            "down_capture": float("nan"),
            "information_ratio": float("nan"),
        }

    variance = float(joined["benchmark"].var(ddof=1))
    covariance = float(joined.cov(ddof=1).loc["strategy", "benchmark"])
    beta = covariance / variance if variance > 0 else float("nan")
    alpha_daily = (
        float(joined["strategy"].mean()) - beta * float(joined["benchmark"].mean())
        if np.isfinite(beta)
        else float("nan")
    )

    up = joined[joined["benchmark"] > 0]
    down = joined[joined["benchmark"] < 0]
    active = joined["strategy"] - joined["benchmark"]
    tracking_error = float(active.std(ddof=1))

    return {
        "beta": beta,
        "alpha_annual": alpha_daily * 252 if np.isfinite(alpha_daily) else float("nan"),
        "correlation": float(joined["strategy"].corr(joined["benchmark"])),
        "up_capture": _capture(up),
        "down_capture": _capture(down),
        "information_ratio": (
            float(active.mean() / tracking_error * np.sqrt(252))
            if tracking_error > 0
            else float("nan")
        ),
    }


def curve_metrics(curve: pd.Series, risk_free_rate: float = 0.0) -> Dict[str, float]:
    """Performance statistics of a benchmark curve.

    A benchmark has no trade log, so the trade-derived statistics come back as
    NaN and the exposure figures describe a position that is always fully
    invested -- which is what a buy-and-hold benchmark is.

    Args:
        curve: The equity curve.
        risk_free_rate: Annualized rate for Sharpe and Sortino.

    Returns:
        The metric dictionary.
    """
    from .engine import EQUITY_COLUMNS
    from .metrics import compute_metrics

    clean = curve.dropna()
    if clean.empty:
        return {}
    frame = pd.DataFrame(
        {
            "equity": clean,
            "cash": 0.0,
            "positions_value": clean,
            "n_positions": 1.0,
            "open_risk": 0.0,
            "regime": True,
        },
        columns=EQUITY_COLUMNS,
    )
    empty_trades = pd.DataFrame(
        columns=["net_pnl_acct", "shares", "entry_price_local", "fx_in", "r_multiple",
                 "bars_held", "costs_acct"]
    )
    return compute_metrics(frame, empty_trades, risk_free_rate, None)


def common_window(curves: Mapping[str, pd.Series]) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    """The date range every supplied curve actually covers.

    Comparing a strategy measured over twenty years against an ETF that only
    existed for ten is not a comparison. This finds the overlap so the report
    can restrict to it and say which window it used.

    Args:
        curves: Mapping of label to equity curve.

    Returns:
        The inclusive overlap, or None when there is no common ground.
    """
    starts, ends = [], []
    for curve in curves.values():
        clean = curve.dropna()
        if clean.empty:
            continue
        starts.append(clean.index[0])
        ends.append(clean.index[-1])
    if not starts:
        return None
    start, end = max(starts), min(ends)
    return (start, end) if start <= end else None


def rebase(curve: pd.Series, start: pd.Timestamp, end: pd.Timestamp,
           initial_equity: float) -> pd.Series:
    """Restrict a curve to a window and restate it from a common starting value.

    Args:
        curve: The equity curve.
        start: Window start.
        end: Window end.
        initial_equity: Value the rebased curve starts at.

    Returns:
        The rebased curve, or an empty series when the window holds no data.
    """
    clean = curve.dropna()
    window = clean[(clean.index >= start) & (clean.index <= end)]
    if window.empty or window.iloc[0] <= 0:
        return pd.Series(dtype=float, name=curve.name)
    return (window / window.iloc[0] * initial_equity).rename(curve.name)


def _capture(subset: pd.DataFrame) -> float:
    """Average strategy return divided by average benchmark return on a subset."""
    if subset.empty:
        return float("nan")
    denominator = float(subset["benchmark"].mean())
    if denominator == 0:
        return float("nan")
    return float(subset["strategy"].mean() / denominator)


def _rate_series(panel: Panel, currency: str) -> pd.Series:
    """Exchange rate series for a currency, or a constant one for the account currency."""
    if currency == panel.fx.account_currency:
        return pd.Series(1.0, index=panel.master_index)
    return panel.fx.series(currency)


def _mark(series: pd.Series, date: pd.Timestamp) -> float:
    """A mark price or rate, treating an unavailable value as zero exposure."""
    value = series.get(date, np.nan)
    return 0.0 if not np.isfinite(value) else float(value)
