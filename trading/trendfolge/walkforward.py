"""Walk-forward validation.

A single backtest over the whole history tells you what parameters would have
worked if you had known the answer in advance. Walk-forward asks a harder and
more useful question: if you had re-tuned the strategy every year using only the
data available at the time, and then traded the next year with those settings,
what would have happened?

The procedure per window is: grid-search on the in-sample years, take the single
best parameter set, apply it unchanged to the following out-of-sample year, and
carry the resulting equity forward into the next window. The out-of-sample
segments stitch into one continuous curve, and **that curve is the result**. The
in-sample numbers are diagnostics; quoting them as performance would be
self-deception.

Two honest caveats about the stitching:

* A position still open when a window ends is marked out at the boundary without
  paying an exit cost, and the next window starts flat. Real trading would have
  held it. This both removes some trend continuation, which hurts, and skips an
  exit cost, which helps; the net effect is small but it is an artifact, not a
  result.
* Walk-forward controls parameter overfitting. It cannot control the overfitting
  embedded in the *choice of rules* -- a twenty-day pullback inside a long
  uptrend on technology stocks was picked by a human who already knows how the
  last fifteen years went. No in-sample/out-of-sample split can measure that.

The grid is deliberately tiny. Twenty-seven combinations against roughly eleven
windows already spends a great deal of the available statistical freedom; a
larger grid is not more rigour, it is more overfitting.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel, build_cost_model
from .datasets.panel import Panel
from .engine import run_backtest
from .metrics import compute_metrics
from .universe import Universe

DEFAULT_GRID_AXES: Dict[str, Sequence[float]] = {
    "atr_stop_mult": (2.0, 2.5, 3.0),
    "atr_trail_mult": (3.0, 3.5, 4.0),
    "pullback_touch_window": (3, 5, 8),
}


@dataclass
class Window:
    """One train/test pair of the walk-forward.

    Attributes:
        train_start, train_end: In-sample range, inclusive.
        test_start, test_end: Out-of-sample range, inclusive.
        chosen: The parameter set selected in sample.
        is_metrics: Metrics of the winning in-sample run.
        oos_metrics: Metrics of the out-of-sample run.
        fallback: True when no candidate met the minimum trade count and the
            defaults were used instead.
        partial: True when the out-of-sample period is materially shorter than
            requested, which happens to the final window when the data runs out.
            Its annualized figures are computed from a stub and mean little on
            their own; only its contribution to the stitched curve does.
    """

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen: Dict[str, float]
    is_metrics: Dict[str, float]
    oos_metrics: Dict[str, float]
    fallback: bool = False
    partial: bool = False


@dataclass
class WalkForwardResult:
    """The outcome of a walk-forward run.

    Attributes:
        windows: One row per window, with the chosen parameters and both metric
            sets.
        equity: The stitched out-of-sample equity curve.
        trades: All out-of-sample trades, in order.
        metrics: Metrics of the stitched curve. These are the headline numbers.
        stability: How consistent the parameter choice was across windows.
    """

    windows: pd.DataFrame
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: Dict[str, float]
    stability: Dict[str, object] = field(default_factory=dict)


def build_grid(axes: Optional[Dict[str, Sequence[float]]] = None) -> List[Dict[str, float]]:
    """Expand the parameter axes into a list of candidate parameter sets.

    Args:
        axes: Mapping of strategy field name to the values to try. Defaults to
            the three-axis grid described in the module docstring.

    Returns:
        Every combination, as a list of override dictionaries.
    """
    axes = axes or DEFAULT_GRID_AXES
    names = list(axes)
    combinations: List[Dict[str, float]] = [{}]
    for name in names:
        expanded = []
        for prefix in combinations:
            for value in axes[name]:
                candidate = dict(prefix)
                candidate[name] = value
                expanded.append(candidate)
        combinations = expanded
    return combinations


def calmar_objective(metrics: Dict[str, float]) -> float:
    """Rank candidates by Calmar ratio, breaking ties with the profit factor.

    Calmar is used rather than Sharpe because Sharpe rewards long quiet
    stretches, which a trend-following strategy produces simply by sitting in
    cash, and rather than raw return because raw return can be carried by a
    single lucky position.

    Args:
        metrics: Metrics of one candidate run.

    Returns:
        The score, minus infinity when undefined.
    """
    calmar = metrics.get("calmar", float("nan"))
    if not np.isfinite(calmar):
        return -np.inf
    tiebreak = metrics.get("profit_factor", 0.0)
    if not np.isfinite(tiebreak):
        tiebreak = 10.0
    return calmar + 1e-6 * tiebreak


def make_windows(
    index: pd.DatetimeIndex,
    train_years: int = 4,
    test_years: int = 1,
    step_years: int = 1,
    anchored: bool = False,
    warmup_bars: int = 0,
) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Lay out the train/test windows over a date index.

    Args:
        index: The master date index.
        train_years: Length of the in-sample period.
        test_years: Length of the out-of-sample period.
        step_years: How far the window advances each iteration.
        anchored: True for an expanding in-sample window that always starts at
            the beginning of the data.
        warmup_bars: Bars reserved at the start so indicators are warm before
            the first in-sample date.

    Returns:
        A list of ``(train_start, train_end, test_start, test_end)`` tuples.
    """
    if len(index) <= warmup_bars:
        return []
    first = index[min(warmup_bars, len(index) - 1)]
    last = index[-1]

    windows = []
    train_start = first
    while True:
        train_end = train_start + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.DateOffset(years=test_years) - pd.Timedelta(days=1)
        if test_start > last:
            break
        test_end = min(test_end, last)
        windows.append(
            (
                first if anchored else train_start,
                train_end,
                test_start,
                test_end,
            )
        )
        train_start = train_start + pd.DateOffset(years=step_years)
    return windows


def run_walk_forward(
    panel: Panel,
    config: Config,
    universe: Universe,
    train_years: int = 4,
    test_years: int = 1,
    step_years: int = 1,
    anchored: bool = False,
    grid: Optional[List[Dict[str, float]]] = None,
    objective: Callable[[Dict[str, float]], float] = calmar_objective,
    min_trades: int = 20,
    cost_model: Optional[CostModel] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> WalkForwardResult:
    """Run a rolling walk-forward over a panel.

    Args:
        panel: Aligned market data covering the whole history.
        config: Base configuration. Grid candidates override its strategy fields.
        universe: The universe definition.
        train_years: In-sample length.
        test_years: Out-of-sample length.
        step_years: Window step.
        anchored: Use an expanding rather than a rolling in-sample window.
        grid: Candidate parameter sets. Defaults to :func:`build_grid`.
        objective: Ranking function applied to the in-sample metrics.
        min_trades: In-sample runs with fewer trades are disqualified, because a
            Calmar computed from five trades is noise.
        cost_model: Cost model, built from the config when omitted.
        progress: Optional callback receiving a status line per window.

    Returns:
        The WalkForwardResult, whose ``metrics`` describe the stitched
        out-of-sample curve.
    """
    grid = grid or build_grid()
    costs = cost_model or build_cost_model(config.costs, universe)
    layout = make_windows(
        panel.master_index,
        train_years=train_years,
        test_years=test_years,
        step_years=step_years,
        anchored=anchored,
        warmup_bars=config.strategy.warmup_bars,
    )
    if not layout:
        raise ValueError(
            "the data range is too short for even one walk-forward window; "
            f"it spans {len(panel.master_index)} bars"
        )

    equity_segments: List[pd.DataFrame] = []
    trade_frames: List[pd.DataFrame] = []
    oos_cost_totals: List[Dict[str, float]] = []
    windows: List[Window] = []
    carried_equity = config.portfolio.initial_equity

    for train_start, train_end, test_start, test_end in layout:
        train_panel = panel.slice(train_start, train_end)

        best_score = -np.inf
        best_candidate: Dict[str, float] = {}
        best_metrics: Dict[str, float] = {}
        for candidate in grid:
            candidate_config = config.with_overrides(strategy=candidate)
            run = run_backtest(train_panel, candidate_config, universe, costs)
            candidate_metrics = compute_metrics(
                run.equity, run.trades, config.backtest.risk_free_rate, run.cost_totals
            )
            if candidate_metrics["n_trades"] < min_trades:
                continue
            score = objective(candidate_metrics)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_metrics = candidate_metrics

        fallback = not best_candidate
        if fallback:
            best_candidate = {}
            best_metrics = {}

        test_config = config.with_overrides(strategy=best_candidate)
        test_panel = panel.slice(test_start, test_end)
        oos = run_backtest(
            test_panel, test_config, universe, costs, initial_equity=carried_equity
        )
        oos_metrics = compute_metrics(
            oos.equity, oos.trades, config.backtest.risk_free_rate, oos.cost_totals
        )

        equity_segments.append(oos.equity)
        oos_cost_totals.append(oos.cost_totals)
        if not oos.trades.empty:
            trade_frames.append(oos.trades)
        carried_equity = oos.final_equity

        windows.append(
            Window(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                chosen=dict(best_candidate),
                is_metrics=best_metrics,
                oos_metrics=oos_metrics,
                fallback=fallback,
                partial=(test_end - test_start).days < 0.8 * 365 * test_years,
            )
        )
        if progress is not None:
            progress(
                f"{test_start.date()}..{test_end.date()} "
                f"params={best_candidate or 'defaults'} "
                f"oos_cagr={oos_metrics['cagr']:.2%} trades={oos_metrics['n_trades']}"
            )

    stitched = pd.concat(equity_segments)
    stitched = stitched[~stitched.index.duplicated(keep="first")]
    trades = (
        pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    )

    cost_totals = _accumulate_costs(oos_cost_totals)

    return WalkForwardResult(
        windows=_windows_frame(windows),
        equity=stitched,
        trades=trades,
        metrics=compute_metrics(
            stitched, trades, config.backtest.risk_free_rate, cost_totals
        ),
        stability=parameter_stability(windows),
    )


def run_fixed_split(
    panel: Panel,
    config: Config,
    universe: Universe,
    in_sample_end: str,
    grid: Optional[List[Dict[str, float]]] = None,
    objective: Callable[[Dict[str, float]], float] = calmar_objective,
    min_trades: int = 20,
    cost_model: Optional[CostModel] = None,
) -> Dict[str, object]:
    """Optimize once on an in-sample period and evaluate once out of sample.

    This is the simplest possible honesty check and complements the rolling
    walk-forward: it uses one long out-of-sample stretch rather than many short
    ones, so it says something about regime change rather than about annual
    re-tuning.

    Args:
        panel: Aligned market data.
        config: Base configuration.
        universe: The universe definition.
        in_sample_end: Last in-sample date, ISO format.
        grid: Candidate parameter sets.
        objective: Ranking function.
        min_trades: Minimum in-sample trade count for a candidate to qualify.
        cost_model: Cost model, built from the config when omitted.

    Returns:
        A dictionary with the chosen parameters and both metric sets.
    """
    grid = grid or build_grid()
    costs = cost_model or build_cost_model(config.costs, universe)
    cut = pd.Timestamp(in_sample_end)

    in_sample = panel.slice(None, cut)
    best_score = -np.inf
    best_candidate: Dict[str, float] = {}
    best_metrics: Dict[str, float] = {}
    for candidate in grid:
        run = run_backtest(in_sample, config.with_overrides(strategy=candidate), universe, costs)
        candidate_metrics = compute_metrics(
            run.equity, run.trades, config.backtest.risk_free_rate, run.cost_totals
        )
        if candidate_metrics["n_trades"] < min_trades:
            continue
        score = objective(candidate_metrics)
        if score > best_score:
            best_score = score
            best_candidate = candidate
            best_metrics = candidate_metrics

    out_of_sample = panel.slice(cut + pd.Timedelta(days=1), None)
    oos_run = run_backtest(
        out_of_sample, config.with_overrides(strategy=best_candidate), universe, costs
    )
    return {
        "chosen": best_candidate,
        "in_sample": best_metrics,
        "out_of_sample": compute_metrics(
            oos_run.equity, oos_run.trades, config.backtest.risk_free_rate, oos_run.cost_totals
        ),
        "out_of_sample_equity": oos_run.equity,
        "out_of_sample_trades": oos_run.trades,
    }


def parameter_stability(windows: Sequence[Window]) -> Dict[str, object]:
    """Describe how consistent the parameter choice was across windows.

    A strategy whose optimal settings jump around from year to year has not
    found a stable effect; it has found noise. The report says so in words
    rather than leaving the reader to infer it from a table.

    Args:
        windows: The completed windows.

    Returns:
        A dictionary with the modal parameter set, how often it was chosen, and
        a plain-language verdict.
    """
    if not windows:
        return {"verdict": "no windows"}

    signatures = [tuple(sorted(window.chosen.items())) for window in windows]
    counts = pd.Series(signatures).value_counts()
    modal_share = float(counts.iloc[0] / len(signatures))
    distinct = int(counts.size)

    per_parameter = {}
    for name in DEFAULT_GRID_AXES:
        values = [window.chosen.get(name) for window in windows if name in window.chosen]
        if values:
            series = pd.Series(values)
            per_parameter[name] = {
                "modal": series.mode().iloc[0],
                "modal_share": float((series == series.mode().iloc[0]).mean()),
                "distinct": int(series.nunique()),
            }

    if modal_share >= 0.6:
        verdict = (
            f"stable: the same parameter set won {modal_share:.0%} of windows, "
            "so the optimizer is finding a persistent effect rather than noise"
        )
    elif distinct >= max(len(windows) - 1, 2):
        verdict = (
            "unstable: almost every window chose a different parameter set, which "
            "is what parameter noise looks like; treat the out-of-sample result as "
            "the only meaningful number and expect the defaults to do about as well"
        )
    else:
        verdict = (
            f"mixed: {distinct} distinct parameter sets across {len(windows)} windows, "
            f"the most common winning {modal_share:.0%} of the time"
        )

    return {
        "modal_parameters": dict(counts.index[0]),
        "modal_share": modal_share,
        "distinct_sets": distinct,
        "n_windows": len(windows),
        "fallback_windows": sum(1 for window in windows if window.fallback),
        "per_parameter": per_parameter,
        "verdict": verdict,
    }


def _accumulate_costs(per_window: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Sum the cost accumulators of every out-of-sample segment.

    Args:
        per_window: One cost-total dictionary per out-of-sample run.

    Returns:
        The summed totals, so the stitched curve reports the friction it really
        paid rather than an estimate.
    """
    totals = {"commission": 0.0, "slippage": 0.0, "total": 0.0, "traded_notional": 0.0}
    for entry in per_window:
        for key in totals:
            totals[key] += float(entry.get(key, 0.0))
    return totals


def _windows_frame(windows: Sequence[Window]) -> pd.DataFrame:
    """Flatten the window records into a reporting table."""
    rows = []
    for window in windows:
        row = {
            "train_start": window.train_start,
            "train_end": window.train_end,
            "test_start": window.test_start,
            "test_end": window.test_end,
            "params": ", ".join(f"{k}={v}" for k, v in sorted(window.chosen.items()))
            or "defaults",
            "fallback": window.fallback,
            "partial": window.partial,
            "is_calmar": window.is_metrics.get("calmar", float("nan")),
            "is_trades": window.is_metrics.get("n_trades", 0),
            "oos_cagr": window.oos_metrics.get("cagr", float("nan")),
            "oos_calmar": window.oos_metrics.get("calmar", float("nan")),
            "oos_max_drawdown": window.oos_metrics.get("max_drawdown", float("nan")),
            "oos_trades": window.oos_metrics.get("n_trades", 0),
            "oos_final_equity": window.oos_metrics.get("final_equity", float("nan")),
        }
        rows.append(row)
    return pd.DataFrame(rows)
