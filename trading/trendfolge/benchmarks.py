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

from typing import Dict, List, Optional

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
