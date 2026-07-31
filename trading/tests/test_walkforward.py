"""Tests for the walk-forward harness.

The property that matters here is that no out-of-sample bar is ever used to
choose the parameters applied to it. That is asserted directly on the window
layout as well as on the result.
"""

import numpy as np
import pandas as pd
import pytest

from trendfolge import walkforward
from trendfolge.datasets.panel import load_panel
from trendfolge.walkforward import (
    build_grid,
    calmar_objective,
    make_windows,
    parameter_stability,
    run_fixed_split,
    run_walk_forward,
)


@pytest.fixture(scope="module")
def _short_panel(synth_dir, synth_universe):
    """An eight-year slice, long enough for several windows and quick to run."""
    from trendfolge.config import Config

    config = Config().with_overrides(
        strategy={"regime_symbol": synth_universe.benchmark_symbol},
        backtest={
            "benchmark_symbol": synth_universe.benchmark_symbol,
            "start": "2005-01-03",
            "end": "2013-12-31",
        },
    )
    return load_panel(synth_dir, synth_universe, config), config


# --- window layout -----------------------------------------------------------


def test_windows_tile_the_range_without_gaps():
    index = pd.bdate_range("2000-01-03", "2020-12-31")
    windows = make_windows(index, train_years=4, test_years=1, step_years=1)

    assert len(windows) > 10
    for _train_start, _train_end, test_start, test_end in windows:
        assert test_start <= test_end
    for previous, following in zip(windows, windows[1:]):
        # Consecutive out-of-sample periods are adjacent, so no year is skipped.
        assert following[2] == previous[2] + pd.DateOffset(years=1)


def test_every_out_of_sample_date_is_strictly_after_its_training_period():
    """The leakage assertion: training may never see the year it is judged on."""
    index = pd.bdate_range("2000-01-03", "2020-12-31")
    windows = make_windows(index, train_years=4, test_years=1)

    for train_start, train_end, test_start, test_end in windows:
        assert train_start < train_end < test_start <= test_end
        assert test_start > train_end


def test_anchored_windows_all_start_at_the_beginning():
    index = pd.bdate_range("2000-01-03", "2020-12-31")
    rolling = make_windows(index, anchored=False)
    anchored = make_windows(index, anchored=True)

    assert len({window[0] for window in anchored}) == 1
    assert len({window[0] for window in rolling}) == len(rolling)
    assert [window[2] for window in rolling] == [window[2] for window in anchored]


def test_warmup_bars_push_the_first_window_forward():
    index = pd.bdate_range("2000-01-03", "2020-12-31")

    without = make_windows(index, warmup_bars=0)
    with_warmup = make_windows(index, warmup_bars=220)

    assert with_warmup[0][0] > without[0][0]
    assert with_warmup[0][0] == index[220]


def test_a_range_too_short_for_one_window_yields_nothing():
    index = pd.bdate_range("2020-01-01", "2020-06-30")

    assert make_windows(index, train_years=4, test_years=1) == []
    assert make_windows(index, warmup_bars=10_000) == []


# --- grid --------------------------------------------------------------------


def test_grid_expands_to_the_full_cartesian_product():
    grid = build_grid({"a": (1, 2, 3), "b": (10, 20)})

    assert len(grid) == 6
    assert {tuple(sorted(entry.items())) for entry in grid} == {
        (("a", 1), ("b", 10)), (("a", 1), ("b", 20)),
        (("a", 2), ("b", 10)), (("a", 2), ("b", 20)),
        (("a", 3), ("b", 10)), (("a", 3), ("b", 20)),
    }


def test_the_default_grid_is_deliberately_small():
    """A bigger grid is more overfitting, not more rigour."""
    assert len(build_grid()) == 27


def test_calmar_objective_rejects_undefined_calmar_and_breaks_ties():
    assert calmar_objective({"calmar": float("nan")}) == -np.inf
    better = calmar_objective({"calmar": 1.0, "profit_factor": 3.0})
    worse = calmar_objective({"calmar": 1.0, "profit_factor": 1.0})
    assert better > worse


# --- full runs ---------------------------------------------------------------


def test_stitched_equity_is_continuous_across_window_boundaries(_short_panel, synth_universe):
    panel, config = _short_panel
    result = run_walk_forward(
        panel,
        config,
        synth_universe,
        train_years=3,
        test_years=1,
        grid=build_grid({"atr_stop_mult": (2.5,)}),
        min_trades=1,
    )

    assert len(result.windows) >= 3
    curve = result.equity["equity"]
    assert curve.index.is_monotonic_increasing
    assert not curve.index.has_duplicates

    # Each window hands its ending equity to the next as starting capital, so
    # the segments join without a jump.
    endings = result.windows["oos_final_equity"].to_numpy()
    for index, ending in enumerate(endings[:-1]):
        next_start_date = result.windows["test_start"].iloc[index + 1]
        first_value = curve.loc[curve.index >= next_start_date].iloc[0]
        assert first_value == pytest.approx(ending, rel=1e-9)


def test_the_stitched_curve_is_the_reported_result(_short_panel, synth_universe):
    panel, config = _short_panel
    result = run_walk_forward(
        panel,
        config,
        synth_universe,
        train_years=3,
        test_years=1,
        grid=build_grid({"atr_stop_mult": (2.5,)}),
        min_trades=1,
    )

    assert result.metrics["final_equity"] == pytest.approx(
        result.equity["equity"].iloc[-1]
    )
    assert result.metrics["n_trades"] == len(result.trades)


def test_no_out_of_sample_trade_falls_inside_its_own_training_window(
    _short_panel, synth_universe
):
    panel, config = _short_panel
    result = run_walk_forward(
        panel,
        config,
        synth_universe,
        train_years=3,
        test_years=1,
        grid=build_grid({"atr_stop_mult": (2.5,)}),
        min_trades=1,
    )

    earliest_test_start = result.windows["test_start"].min()
    assert (result.trades["entry_date"] >= earliest_test_start).all()


def test_an_impossible_minimum_trade_count_forces_the_fallback(
    _short_panel, synth_universe
):
    panel, config = _short_panel
    result = run_walk_forward(
        panel,
        config,
        synth_universe,
        train_years=3,
        test_years=1,
        grid=build_grid({"atr_stop_mult": (2.0, 3.0)}),
        min_trades=10_000,
    )

    assert result.windows["fallback"].all()
    assert (result.windows["params"] == "defaults").all()
    assert result.stability["fallback_windows"] == len(result.windows)


def test_a_partial_final_window_is_flagged(_short_panel, synth_universe):
    panel, config = _short_panel
    result = run_walk_forward(
        panel,
        config,
        synth_universe,
        train_years=3,
        test_years=1,
        grid=build_grid({"atr_stop_mult": (2.5,)}),
        min_trades=1,
    )

    assert "partial" in result.windows.columns
    assert not result.windows["partial"].iloc[:-1].any()


def test_a_panel_too_short_for_any_window_raises(_short_panel, synth_universe):
    panel, config = _short_panel
    tiny = panel.slice(panel.master_index[0], panel.master_index[300])

    with pytest.raises(ValueError, match="too short"):
        run_walk_forward(tiny, config, synth_universe, grid=build_grid({"atr_stop_mult": (2.5,)}))


def test_fixed_split_evaluates_out_of_sample_only_once(_short_panel, synth_universe):
    panel, config = _short_panel
    outcome = run_fixed_split(
        panel,
        config,
        synth_universe,
        in_sample_end="2011-12-31",
        grid=build_grid({"atr_stop_mult": (2.0, 2.5, 3.0)}),
        min_trades=1,
    )

    assert set(outcome) >= {"chosen", "in_sample", "out_of_sample"}
    assert outcome["out_of_sample_equity"].index.min() > pd.Timestamp("2011-12-31")
    assert outcome["in_sample"]["n_trades"] > 0


# --- parameter stability -----------------------------------------------------


def _window(chosen):
    return walkforward.Window(
        train_start=pd.Timestamp("2010-01-01"),
        train_end=pd.Timestamp("2013-12-31"),
        test_start=pd.Timestamp("2014-01-01"),
        test_end=pd.Timestamp("2014-12-31"),
        chosen=chosen,
        is_metrics={},
        oos_metrics={},
    )


def test_stability_reports_a_stable_choice():
    windows = [_window({"atr_stop_mult": 2.5}) for _ in range(5)]

    stability = parameter_stability(windows)

    assert stability["modal_share"] == 1.0
    assert stability["distinct_sets"] == 1
    assert "stable" in stability["verdict"]


def test_stability_calls_out_a_wandering_optimizer():
    windows = [_window({"atr_stop_mult": value}) for value in (2.0, 2.5, 3.0, 3.5, 4.0)]

    stability = parameter_stability(windows)

    assert stability["distinct_sets"] == 5
    assert "unstable" in stability["verdict"]
    assert "noise" in stability["verdict"]


def test_stability_of_an_empty_run_does_not_raise():
    assert parameter_stability([])["verdict"] == "no windows"
