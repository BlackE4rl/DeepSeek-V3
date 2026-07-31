"""Performance statistics.

Every figure is derived from the daily account-currency equity series and the
trade log. Degenerate inputs -- a flat equity curve, a run with no losing trade,
a window with no trades at all -- return NaN or infinity rather than raising, so
a sweep over hundreds of parameter combinations does not die on one empty cell.

The reader should treat the trade-derived statistics with more suspicion than
the equity-derived ones: a few hundred trades is a small sample, and the
standard error on a Sharpe or a Calmar computed from it is large. That is what
the bootstrap in ``sensitivity.py`` is for.
"""

from typing import Dict, Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DAYS_PER_YEAR = 365.25


def daily_returns(equity: pd.Series) -> pd.Series:
    """Simple bar-to-bar returns of an equity curve."""
    return equity.pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    """Cumulative return over the whole series."""
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    """Compound annual growth rate, measured on calendar time.

    Args:
        equity: The equity curve, indexed by date.

    Returns:
        The annualized growth rate, or NaN if the series is too short or the
        account was wiped out.
    """
    if len(equity) < 2 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return float("nan")
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return float("nan")
    years = days / DAYS_PER_YEAR
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def annualized_volatility(returns: pd.Series) -> float:
    """Annualized standard deviation of bar returns."""
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sharpe ratio.

    Args:
        returns: Daily returns.
        risk_free_rate: Annualized risk-free rate.

    Returns:
        The ratio, or NaN when the return series has no variation.
    """
    if len(returns) < 2:
        return float("nan")
    excess = returns - risk_free_rate / TRADING_DAYS
    deviation = excess.std(ddof=1)
    if not np.isfinite(deviation) or deviation == 0:
        return float("nan")
    return float(excess.mean() / deviation * np.sqrt(TRADING_DAYS))


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sortino ratio, penalising downside deviation only."""
    if len(returns) < 2:
        return float("nan")
    excess = returns - risk_free_rate / TRADING_DAYS
    downside = excess.clip(upper=0.0)
    deviation = np.sqrt((downside**2).mean())
    if not np.isfinite(deviation) or deviation == 0:
        return float("inf") if excess.mean() > 0 else float("nan")
    return float(excess.mean() / deviation * np.sqrt(TRADING_DAYS))


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak, at every bar."""
    peak = equity.cummax()
    return equity / peak - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """Deepest peak-to-trough decline, as a negative fraction."""
    if equity.empty:
        return float("nan")
    return float(drawdown_series(equity).min())


def max_drawdown_duration_days(equity: pd.Series) -> float:
    """Longest stretch, in calendar days, spent below a previous peak."""
    if equity.empty:
        return float("nan")
    underwater = drawdown_series(equity) < 0
    longest = 0.0
    start: Optional[pd.Timestamp] = None
    for date, below in underwater.items():
        if below and start is None:
            start = date
        elif not below and start is not None:
            longest = max(longest, (date - start).days)
            start = None
    if start is not None:
        longest = max(longest, (underwater.index[-1] - start).days)
    return float(longest)


def calmar_ratio(equity: pd.Series) -> float:
    """CAGR divided by the absolute maximum drawdown."""
    drawdown = max_drawdown(equity)
    growth = cagr(equity)
    if not np.isfinite(drawdown) or drawdown == 0 or not np.isfinite(growth):
        return float("nan")
    return float(growth / abs(drawdown))


def ulcer_index(equity: pd.Series) -> float:
    """Root mean square drawdown: depth and duration of pain in one number."""
    if equity.empty:
        return float("nan")
    drawdown = drawdown_series(equity)
    return float(np.sqrt((drawdown**2).mean()))


def monthly_returns(equity: pd.Series) -> pd.Series:
    """Calendar month returns of an equity curve."""
    if equity.empty:
        return pd.Series(dtype=float)
    month_end = equity.resample("ME").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0]])
    joined = pd.concat([first, month_end])
    return joined.pct_change().dropna()


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Calendar year returns of an equity curve."""
    if equity.empty:
        return pd.Series(dtype=float)
    year_end = equity.resample("YE").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0]])
    joined = pd.concat([first, year_end])
    return joined.pct_change().dropna()


def trade_statistics(trades: pd.DataFrame) -> Dict[str, float]:
    """Summary statistics of a trade log.

    Args:
        trades: The completed round trips.

    Returns:
        A dictionary of trade-level statistics. An empty log yields NaN
        everywhere rather than raising, so parameter sweeps stay runnable.
    """
    if trades.empty:
        return {
            "n_trades": 0,
            "hit_rate": float("nan"),
            "avg_win_pct": float("nan"),
            "avg_loss_pct": float("nan"),
            "payoff_ratio": float("nan"),
            "profit_factor": float("nan"),
            "expectancy_r": float("nan"),
            "avg_bars_held": float("nan"),
            "median_bars_held": float("nan"),
            "max_consecutive_losses": 0,
            "avg_costs_per_trade": float("nan"),
        }

    net = trades["net_pnl_acct"]
    wins = net[net > 0]
    losses = net[net < 0]

    gross_wins = float(wins.sum())
    gross_losses = float(-losses.sum())
    if gross_losses > 0:
        profit_factor = gross_wins / gross_losses
    else:
        profit_factor = float("inf") if gross_wins > 0 else float("nan")

    entry_value = trades["shares"] * trades["entry_price_local"] * trades["fx_in"]
    pct = net / entry_value.replace(0.0, np.nan)
    win_pct = pct[net > 0]
    loss_pct = pct[net < 0]

    avg_win = float(win_pct.mean()) if len(win_pct) else float("nan")
    avg_loss = float(loss_pct.mean()) if len(loss_pct) else float("nan")
    if np.isfinite(avg_win) and np.isfinite(avg_loss) and avg_loss != 0:
        payoff = float(abs(avg_win / avg_loss))
    else:
        payoff = float("nan")

    return {
        "n_trades": int(len(trades)),
        "hit_rate": float((net > 0).mean()),
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": payoff,
        "profit_factor": profit_factor,
        "expectancy_r": float(trades["r_multiple"].replace([np.inf, -np.inf], np.nan).mean()),
        "avg_bars_held": float(trades["bars_held"].mean()),
        "median_bars_held": float(trades["bars_held"].median()),
        "max_consecutive_losses": _max_consecutive_losses(net),
        "avg_costs_per_trade": float(trades["costs_acct"].mean()),
    }


def exposure_statistics(equity: pd.DataFrame) -> Dict[str, float]:
    """How much of the time and of the capital the strategy actually deployed."""
    if equity.empty:
        return {
            "avg_invested_fraction": float("nan"),
            "avg_open_positions": float("nan"),
            "days_with_a_position": float("nan"),
            "max_open_positions": 0,
        }
    invested = equity["positions_value"] / equity["equity"].replace(0.0, np.nan)
    return {
        "avg_invested_fraction": float(invested.mean()),
        "avg_open_positions": float(equity["n_positions"].mean()),
        "days_with_a_position": float((equity["n_positions"] > 0).mean()),
        "max_open_positions": int(equity["n_positions"].max()),
    }


def cost_statistics(
    equity: pd.DataFrame, cost_totals: Dict[str, float]
) -> Dict[str, float]:
    """Turnover and the drag the cost model imposed."""
    if equity.empty:
        return {}
    average_equity = float(equity["equity"].mean())
    days = (equity.index[-1] - equity.index[0]).days
    years = max(days / DAYS_PER_YEAR, 1e-9)

    traded = float(cost_totals.get("traded_notional", 0.0))
    total_costs = float(cost_totals.get("total", 0.0))
    turnover = traded / average_equity / years if average_equity > 0 else float("nan")
    drag_bps = (
        total_costs / average_equity / years * 1e4 if average_equity > 0 else float("nan")
    )
    return {
        "annual_turnover": turnover,
        "total_commission": float(cost_totals.get("commission", 0.0)),
        "total_slippage": float(cost_totals.get("slippage", 0.0)),
        "total_costs": total_costs,
        "cost_drag_bps_per_year": drag_bps,
    }


def compute_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    risk_free_rate: float = 0.0,
    cost_totals: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Compute the full metric set for one run.

    Args:
        equity: The daily account state, with at least an ``equity`` column.
        trades: The completed round trips.
        risk_free_rate: Annualized risk-free rate for Sharpe and Sortino.
        cost_totals: Cost accumulators from the run.

    Returns:
        A flat dictionary of metric name to value.
    """
    curve = equity["equity"]
    returns = daily_returns(curve)
    monthly = monthly_returns(curve)

    metrics: Dict[str, float] = {
        "start": equity.index[0] if len(equity) else pd.NaT,
        "end": equity.index[-1] if len(equity) else pd.NaT,
        "final_equity": float(curve.iloc[-1]) if len(curve) else float("nan"),
        "total_return": total_return(curve),
        "cagr": cagr(curve),
        "annual_volatility": annualized_volatility(returns),
        "sharpe": sharpe_ratio(returns, risk_free_rate),
        "sortino": sortino_ratio(returns, risk_free_rate),
        "max_drawdown": max_drawdown(curve),
        "max_drawdown_days": max_drawdown_duration_days(curve),
        "calmar": calmar_ratio(curve),
        "ulcer_index": ulcer_index(curve),
        "best_month": float(monthly.max()) if len(monthly) else float("nan"),
        "worst_month": float(monthly.min()) if len(monthly) else float("nan"),
        "positive_months": float((monthly > 0).mean()) if len(monthly) else float("nan"),
    }
    metrics.update(exposure_statistics(equity))
    metrics.update(trade_statistics(trades))
    if cost_totals:
        metrics.update(cost_statistics(equity, cost_totals))
    return metrics


def exit_reason_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    """Count, share and average P&L per exit reason."""
    if trades.empty:
        return pd.DataFrame(columns=["count", "share", "avg_net_pnl", "hit_rate"])
    grouped = trades.groupby("exit_reason")["net_pnl_acct"]
    frame = pd.DataFrame(
        {
            "count": grouped.size(),
            "avg_net_pnl": grouped.mean(),
            "hit_rate": grouped.apply(lambda values: float((values > 0).mean())),
        }
    )
    frame["share"] = frame["count"] / frame["count"].sum()
    return frame[["count", "share", "avg_net_pnl", "hit_rate"]].sort_values(
        "count", ascending=False
    )


def currency_attribution(trades: pd.DataFrame) -> pd.DataFrame:
    """Split each currency's contribution into the local move and the FX move.

    An EUR-denominated account holding US technology stocks earns a meaningful
    part of its return from the euro-dollar rate. Reporting that separately is
    what keeps a currency tailwind from being mistaken for strategy edge.

    Args:
        trades: The completed round trips.

    Returns:
        One row per currency with the mean local return, the mean FX return and
        the summed net P&L.
    """
    if trades.empty:
        return pd.DataFrame(columns=["n_trades", "avg_local_return", "avg_fx_return", "net_pnl"])
    grouped = trades.groupby("currency")
    return pd.DataFrame(
        {
            "n_trades": grouped.size(),
            "avg_local_return": grouped["local_return"].mean(),
            "avg_fx_return": grouped["fx_return"].mean(),
            "net_pnl": grouped["net_pnl_acct"].sum(),
        }
    ).sort_values("net_pnl", ascending=False)


def _max_consecutive_losses(net: pd.Series) -> int:
    """Longest run of consecutive losing trades."""
    longest = 0
    current = 0
    for value in net:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)
