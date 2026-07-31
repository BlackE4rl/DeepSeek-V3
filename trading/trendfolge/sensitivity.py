"""Robustness analysis.

A backtest produces one number per metric. That number is a point estimate drawn
from a sample of a few hundred trades, chosen from a parameter set that was
itself selected by looking at the data. Four things are done here to put error
bars around it:

* one-factor-at-a-time sweeps, to see whether the result survives moving a
  parameter or sits on a single sharp peak;
* a two-dimensional sweep of the two stop parameters, which interact;
* a bootstrap over the trade sequence, which turns the point estimate into a
  fifth-to-ninety-fifth percentile band;
* a cost stress test at one, two and four times the modelled friction.

The verdict rule is fixed in advance and stated in the output, so it cannot be
adjusted after seeing the answer: the strategy is called *plausibly robust* only
if at least seventy percent of the sampled parameter neighbourhood keeps a
Calmar above half the best Calmar, with no sign flip in the growth rate.
Otherwise the report says **curve-fit**, in those words.
"""

from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .config import Config, ConfigError
from .costs import CostModel, build_cost_model
from .datasets.panel import Panel
from .engine import run_backtest
from .metrics import compute_metrics
from .universe import Universe

DEFAULT_SWEEPS: Dict[str, Sequence[float]] = {
    "atr_stop_mult": (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0),
    "atr_trail_mult": (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0),
    # Stays below sma_mid: the configuration requires
    # sma_pullback < sma_mid < sma_long, so 50 would be rejected outright.
    "sma_pullback": (5, 10, 15, 20, 25, 30, 40),
    "sma_long": (100, 150, 180, 200, 220, 250, 300),
    "pullback_touch_window": (1, 2, 3, 5, 8, 10, 15),
    "pullback_max_depth_atr": (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0),
}

PORTFOLIO_SWEEPS: Dict[str, Sequence[float]] = {
    "risk_per_trade": (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03),
    "max_positions": (2, 3, 4, 6, 8, 10, 12),
}

REPORTED_METRICS = ["cagr", "calmar", "sharpe", "max_drawdown", "n_trades", "final_equity"]


def _evaluate(
    panel: Panel,
    config: Config,
    universe: Universe,
    costs: Optional[CostModel] = None,
) -> Dict[str, float]:
    """Run one configuration and return its metrics."""
    model = costs or build_cost_model(config.costs, universe)
    run = run_backtest(panel, config, universe, model)
    return compute_metrics(
        run.equity, run.trades, config.backtest.risk_free_rate, run.cost_totals
    )


def sweep_parameter(
    panel: Panel,
    config: Config,
    universe: Universe,
    section: str,
    name: str,
    values: Sequence[float],
) -> pd.DataFrame:
    """Vary one parameter while holding everything else at its default.

    Args:
        panel: Aligned market data.
        config: Base configuration.
        universe: The universe definition.
        section: Configuration section the parameter belongs to.
        name: Field name.
        values: Values to try.

    Returns:
        One row per value with the reported metrics.
    """
    rows = []
    for value in values:
        row = {"parameter": name, "value": value}
        try:
            candidate = config.with_overrides(**{section: {name: value}})
            metrics = _evaluate(panel, candidate, universe)
        except ConfigError as error:
            # Some points of a sweep are simply not valid configurations, for
            # instance a pullback average that is not shorter than the mid
            # average. Record them as gaps rather than aborting a run that may
            # already have taken several minutes.
            row.update({key: float("nan") for key in REPORTED_METRICS})
            row["invalid"] = str(error)
            rows.append(row)
            continue
        row.update({key: metrics.get(key, float("nan")) for key in REPORTED_METRICS})
        row["invalid"] = ""
        rows.append(row)
    return pd.DataFrame(rows)


def sweep_all(
    panel: Panel,
    config: Config,
    universe: Universe,
    strategy_axes: Optional[Dict[str, Sequence[float]]] = None,
    portfolio_axes: Optional[Dict[str, Sequence[float]]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Run every one-factor-at-a-time sweep.

    Args:
        panel: Aligned market data.
        config: Base configuration.
        universe: The universe definition.
        strategy_axes: Strategy parameters and the values to try.
        portfolio_axes: Portfolio parameters and the values to try.
        progress: Optional status callback.

    Returns:
        The concatenated sweep table.
    """
    strategy_axes = DEFAULT_SWEEPS if strategy_axes is None else strategy_axes
    portfolio_axes = PORTFOLIO_SWEEPS if portfolio_axes is None else portfolio_axes

    frames = []
    for name, values in strategy_axes.items():
        if progress is not None:
            progress(f"sweeping strategy.{name}")
        frames.append(sweep_parameter(panel, config, universe, "strategy", name, values))
    for name, values in portfolio_axes.items():
        if progress is not None:
            progress(f"sweeping portfolio.{name}")
        frames.append(sweep_parameter(panel, config, universe, "portfolio", name, values))
    if not frames:
        return pd.DataFrame(columns=["parameter", "value"] + REPORTED_METRICS)
    return pd.concat(frames, ignore_index=True)


def sweep_two(
    panel: Panel,
    config: Config,
    universe: Universe,
    first: str,
    first_values: Sequence[float],
    second: str,
    second_values: Sequence[float],
    metric: str = "calmar",
) -> pd.DataFrame:
    """Sweep two interacting strategy parameters jointly.

    Args:
        panel: Aligned market data.
        config: Base configuration.
        universe: The universe definition.
        first: First strategy field name, used as the row index.
        first_values: Values for the first parameter.
        second: Second strategy field name, used as the columns.
        second_values: Values for the second parameter.
        metric: Which metric to place in the cells.

    Returns:
        A matrix of the metric, indexed by ``first_values``.
    """
    matrix = pd.DataFrame(index=list(first_values), columns=list(second_values), dtype=float)
    for row_value in first_values:
        for column_value in second_values:
            try:
                candidate = config.with_overrides(
                    strategy={first: row_value, second: column_value}
                )
                metrics = _evaluate(panel, candidate, universe)
            except ConfigError:
                matrix.loc[row_value, column_value] = float("nan")
                continue
            matrix.loc[row_value, column_value] = metrics.get(metric, float("nan"))
    matrix.index.name = first
    matrix.columns.name = second
    return matrix


def cost_stress(
    panel: Panel,
    config: Config,
    universe: Universe,
    factors: Sequence[float] = (0.0, 1.0, 2.0, 4.0),
) -> pd.DataFrame:
    """Rerun the strategy at multiples of the modelled transaction costs.

    Args:
        panel: Aligned market data.
        config: Base configuration.
        universe: The universe definition.
        factors: Cost multipliers. Zero yields the frictionless run.

    Returns:
        One row per factor with the reported metrics.
    """
    rows = []
    for factor in factors:
        scaled = config.costs.scaled(factor)
        candidate = config.with_overrides(
            costs={field: getattr(scaled, field) for field in scaled.__dataclass_fields__}
        )
        metrics = _evaluate(panel, candidate, universe)
        row = {"cost_factor": factor}
        row.update({key: metrics.get(key, float("nan")) for key in REPORTED_METRICS})
        row["cost_drag_bps_per_year"] = metrics.get("cost_drag_bps_per_year", float("nan"))
        rows.append(row)
    return pd.DataFrame(rows)


def bootstrap_trades(
    trades: pd.DataFrame,
    initial_equity: float,
    n_samples: int = 1000,
    seed: int = 12345,
) -> pd.DataFrame:
    """Resample the trade sequence with replacement.

    Each trade's impact is expressed as a fraction of the equity that was at work
    when it was decided on, and the resampled fractions are compounded. This
    treats the strategy as a machine that draws trades from a fixed distribution
    -- which is exactly the assumption worth stress-testing, because if the real
    distribution shifts, none of these numbers hold.

    Args:
        trades: The trade log. Must carry a ``pnl_fraction`` column.
        initial_equity: Starting capital.
        n_samples: Number of bootstrap replications.
        seed: RNG seed.

    Returns:
        One row per replication with the terminal equity, total return and the
        deepest drawdown of the resampled trade sequence.
    """
    if trades.empty or "pnl_fraction" not in trades.columns:
        return pd.DataFrame(columns=["terminal_equity", "total_return", "max_drawdown"])

    fractions = trades["pnl_fraction"].replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
    if fractions.size == 0:
        return pd.DataFrame(columns=["terminal_equity", "total_return", "max_drawdown"])

    rng = np.random.default_rng(seed)
    draws = rng.choice(fractions, size=(n_samples, fractions.size), replace=True)
    paths = initial_equity * np.cumprod(1.0 + draws, axis=1)

    running_peak = np.maximum.accumulate(paths, axis=1)
    drawdowns = (paths / running_peak - 1.0).min(axis=1)

    terminal = paths[:, -1]
    return pd.DataFrame(
        {
            "terminal_equity": terminal,
            "total_return": terminal / initial_equity - 1.0,
            "max_drawdown": drawdowns,
        }
    )


def bootstrap_summary(bootstrap: pd.DataFrame) -> Dict[str, float]:
    """Percentile bands of a bootstrap run.

    Args:
        bootstrap: Output of :func:`bootstrap_trades`.

    Returns:
        The 5th, 50th and 95th percentiles of terminal equity, total return and
        maximum drawdown. Report the band, never the point estimate: with a few
        hundred trades the band is wide, and pretending otherwise is the whole
        problem with published backtests.
    """
    if bootstrap.empty:
        return {}
    summary: Dict[str, float] = {}
    for column in ("terminal_equity", "total_return", "max_drawdown"):
        for percentile in (5, 50, 95):
            summary[f"{column}_p{percentile}"] = float(
                np.percentile(bootstrap[column], percentile)
            )
    return summary


def robustness_verdict(
    sweeps: pd.DataFrame, threshold_share: float = 0.7, calmar_fraction: float = 0.5
) -> Dict[str, object]:
    """Apply the pre-declared robustness rule to the sweep results.

    The rule is fixed before the data is seen so that it cannot be relaxed once
    the answer is known.

    Args:
        sweeps: Output of :func:`sweep_all`.
        threshold_share: Fraction of the neighbourhood that must hold up.
        calmar_fraction: Fraction of the best Calmar a point must retain.

    Returns:
        A dictionary with the computed share, the verdict string and the rule
        that produced it.
    """
    rule = (
        f"plausibly robust requires at least {threshold_share:.0%} of sampled parameter "
        f"values to keep a Calmar above {calmar_fraction:.0%} of the best Calmar, "
        "with no sign flip in CAGR"
    )
    if sweeps.empty:
        return {"share_holding_up": float("nan"), "verdict": "no data", "rule": rule}

    # Points that are not valid configurations at all are gaps in the sample,
    # not evidence of fragility, so they are excluded rather than counted as
    # failures.
    if "invalid" in sweeps.columns:
        sweeps = sweeps[sweeps["invalid"].fillna("") == ""]
    if sweeps.empty:
        return {"share_holding_up": float("nan"), "verdict": "no data", "rule": rule}

    calmar = sweeps["calmar"].replace([np.inf, -np.inf], np.nan)
    best = float(calmar.max()) if calmar.notna().any() else float("nan")
    if not np.isfinite(best) or best <= 0:
        return {
            "share_holding_up": 0.0,
            "verdict": "curve-fit: the best parameter set does not even produce a "
            "positive risk-adjusted return, so there is nothing to be robust about",
            "rule": rule,
            "best_calmar": best,
        }

    holds = (calmar >= calmar_fraction * best) & (sweeps["cagr"] > 0)
    share = float(holds.mean())

    if share >= threshold_share:
        verdict = (
            f"plausibly robust: {share:.0%} of the sampled parameter neighbourhood "
            f"keeps a Calmar above {calmar_fraction:.0%} of the best, with a positive "
            "growth rate throughout"
        )
    else:
        verdict = (
            f"curve-fit: only {share:.0%} of the sampled neighbourhood holds up, so "
            "the headline result sits on a narrow peak rather than a plateau; expect "
            "live performance to look like the surrounding mush, not like the peak"
        )

    fragile = _fragile_parameters(sweeps, best, calmar_fraction)
    return {
        "share_holding_up": share,
        "verdict": verdict,
        "rule": rule,
        "best_calmar": best,
        "fragile_parameters": fragile,
    }


def _fragile_parameters(
    sweeps: pd.DataFrame, best: float, calmar_fraction: float
) -> List[str]:
    """Parameters whose sweep collapses over most of its range."""
    fragile = []
    for name, group in sweeps.groupby("parameter"):
        calmar = group["calmar"].replace([np.inf, -np.inf], np.nan)
        holds = (calmar >= calmar_fraction * best) & (group["cagr"] > 0)
        if float(holds.mean()) < 0.5:
            fragile.append(str(name))
    return sorted(fragile)
