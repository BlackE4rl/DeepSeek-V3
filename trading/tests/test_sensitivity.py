"""Tests for the robustness analysis."""

import numpy as np
import pandas as pd
import pytest

from trendfolge import sensitivity
from trendfolge.config import Config
from trendfolge.datasets.panel import load_panel


@pytest.fixture(scope="module")
def _short_panel(synth_dir, synth_universe):
    config = Config().with_overrides(
        strategy={"regime_symbol": synth_universe.benchmark_symbol},
        backtest={
            "benchmark_symbol": synth_universe.benchmark_symbol,
            "start": "2005-01-03",
            "end": "2012-12-31",
        },
    )
    return load_panel(synth_dir, synth_universe, config), config


# --- sweeps ------------------------------------------------------------------


def test_sweep_returns_one_row_per_value(_short_panel, synth_universe):
    panel, config = _short_panel
    values = (2.0, 2.5, 3.0)

    frame = sensitivity.sweep_parameter(
        panel, config, synth_universe, "strategy", "atr_stop_mult", values
    )

    assert list(frame["value"]) == list(values)
    assert (frame["parameter"] == "atr_stop_mult").all()
    assert set(sensitivity.REPORTED_METRICS) <= set(frame.columns)
    assert frame["n_trades"].gt(0).all()


def test_a_sweep_actually_changes_the_outcome(_short_panel, synth_universe):
    """If a parameter never moves the result it is not a parameter."""
    panel, config = _short_panel

    frame = sensitivity.sweep_parameter(
        panel, config, synth_universe, "strategy", "atr_stop_mult", (1.5, 3.0, 5.0)
    )

    assert frame["final_equity"].nunique() == 3


def test_an_invalid_sweep_point_is_recorded_as_a_gap_not_a_crash(
    _short_panel, synth_universe
):
    """A long sweep must not abort because one point is not a legal config."""
    panel, config = _short_panel

    # sma_pullback must stay below sma_mid, so 50 and 80 are rejected outright.
    frame = sensitivity.sweep_parameter(
        panel, config, synth_universe, "strategy", "sma_pullback", (20, 50, 80)
    )

    assert len(frame) == 3
    assert frame["invalid"].iloc[0] == ""
    assert "sma_pullback < sma_mid" in frame["invalid"].iloc[1]
    assert np.isnan(frame["calmar"].iloc[1])
    assert np.isfinite(frame["calmar"].iloc[0])


def test_invalid_points_do_not_count_against_the_robustness_verdict():
    valid = _sweep_table([1.0, 1.0, 1.0], [0.1, 0.1, 0.1])
    valid["invalid"] = ""
    gap = _sweep_table([np.nan], [np.nan])
    gap["invalid"] = "not a legal configuration"
    table = pd.concat([valid, gap], ignore_index=True)

    verdict = sensitivity.robustness_verdict(table)

    assert verdict["share_holding_up"] == pytest.approx(1.0)
    assert "plausibly robust" in verdict["verdict"]


def test_the_default_pullback_sweep_stays_below_the_mid_average():
    assert max(sensitivity.DEFAULT_SWEEPS["sma_pullback"]) < 50


def test_sweep_all_covers_every_requested_axis(_short_panel, synth_universe):
    panel, config = _short_panel

    frame = sensitivity.sweep_all(
        panel,
        config,
        synth_universe,
        strategy_axes={"atr_stop_mult": (2.0, 3.0)},
        portfolio_axes={"max_positions": (3, 6)},
    )

    assert set(frame["parameter"]) == {"atr_stop_mult", "max_positions"}
    assert len(frame) == 4


def test_two_dimensional_sweep_is_a_labelled_matrix(_short_panel, synth_universe):
    panel, config = _short_panel

    matrix = sensitivity.sweep_two(
        panel, config, synth_universe,
        "atr_stop_mult", (2.0, 3.0),
        "atr_trail_mult", (3.0, 4.0),
    )

    assert matrix.shape == (2, 2)
    assert matrix.index.name == "atr_stop_mult"
    assert matrix.columns.name == "atr_trail_mult"
    assert matrix.notna().any().any()


def test_cost_stress_is_monotonic_in_the_cost_factor(_short_panel, synth_universe):
    """More friction cannot make the same strategy richer."""
    panel, config = _short_panel

    frame = sensitivity.cost_stress(panel, config, synth_universe, factors=(0.0, 1.0, 4.0))

    assert list(frame["cost_factor"]) == [0.0, 1.0, 4.0]
    assert frame["final_equity"].iloc[0] > frame["final_equity"].iloc[2]
    assert frame["cost_drag_bps_per_year"].iloc[0] == pytest.approx(0.0)


# --- bootstrap ---------------------------------------------------------------


def _trade_log(fractions):
    return pd.DataFrame(
        {
            "pnl_fraction": list(fractions),
            "net_pnl_acct": [value * 100_000 for value in fractions],
        }
    )


def test_bootstrap_is_deterministic_for_a_given_seed():
    trades = _trade_log([0.01, -0.005, 0.02, -0.005, 0.015])

    first = sensitivity.bootstrap_trades(trades, 100_000.0, n_samples=200, seed=1)
    second = sensitivity.bootstrap_trades(trades, 100_000.0, n_samples=200, seed=1)
    different = sensitivity.bootstrap_trades(trades, 100_000.0, n_samples=200, seed=2)

    pd.testing.assert_frame_equal(first, second)
    assert not first["terminal_equity"].equals(different["terminal_equity"])


def test_bootstrap_of_a_uniformly_positive_log_never_loses_money():
    trades = _trade_log([0.01] * 20)

    outcome = sensitivity.bootstrap_trades(trades, 100_000.0, n_samples=100, seed=3)

    assert (outcome["total_return"] > 0).all()
    assert (outcome["max_drawdown"] == 0).all()
    assert outcome["terminal_equity"].nunique() == 1


def test_bootstrap_produces_a_band_not_a_point():
    rng = np.random.default_rng(5)
    trades = _trade_log(rng.normal(0.002, 0.02, 200))

    outcome = sensitivity.bootstrap_trades(trades, 100_000.0, n_samples=500, seed=4)
    summary = sensitivity.bootstrap_summary(outcome)

    assert summary["total_return_p5"] < summary["total_return_p50"] < summary["total_return_p95"]
    assert summary["max_drawdown_p5"] <= summary["max_drawdown_p95"] <= 0


def test_bootstrap_of_an_empty_or_degenerate_log_is_empty():
    assert sensitivity.bootstrap_trades(pd.DataFrame(), 100_000.0).empty
    assert sensitivity.bootstrap_summary(pd.DataFrame()) == {}

    only_nan = pd.DataFrame({"pnl_fraction": [np.nan, np.inf]})
    assert sensitivity.bootstrap_trades(only_nan, 100_000.0).empty


# --- verdict -----------------------------------------------------------------


def _sweep_table(calmars, cagrs, parameter="p"):
    return pd.DataFrame(
        {
            "parameter": [parameter] * len(calmars),
            "value": range(len(calmars)),
            "calmar": calmars,
            "cagr": cagrs,
        }
    )


def test_a_broad_plateau_is_called_plausibly_robust():
    table = _sweep_table([1.0, 0.9, 1.1, 0.95, 1.05], [0.1] * 5)

    verdict = sensitivity.robustness_verdict(table)

    assert verdict["share_holding_up"] == pytest.approx(1.0)
    assert "plausibly robust" in verdict["verdict"]


def test_a_single_sharp_peak_is_called_curve_fit():
    table = _sweep_table([2.0, 0.1, 0.05, 0.02, 0.01], [0.2, 0.01, 0.005, 0.001, 0.0005])

    verdict = sensitivity.robustness_verdict(table)

    assert verdict["share_holding_up"] < 0.7
    assert "curve-fit" in verdict["verdict"]


def test_a_negative_best_calmar_is_curve_fit_by_definition():
    table = _sweep_table([-0.5, -1.0, -0.2], [-0.05, -0.1, -0.02])

    verdict = sensitivity.robustness_verdict(table)

    assert "curve-fit" in verdict["verdict"]
    assert verdict["share_holding_up"] == 0.0


def test_a_negative_growth_rate_disqualifies_a_point_even_with_a_good_calmar():
    table = _sweep_table([1.0, 1.0, 1.0, 1.0], [0.1, -0.1, -0.1, -0.1])

    verdict = sensitivity.robustness_verdict(table)

    assert verdict["share_holding_up"] == pytest.approx(0.25)
    assert "curve-fit" in verdict["verdict"]


def test_the_rule_is_stated_alongside_the_verdict():
    """The threshold is declared in the output so it cannot be moved afterwards."""
    verdict = sensitivity.robustness_verdict(_sweep_table([1.0], [0.1]))

    assert "70%" in verdict["rule"]
    assert "50%" in verdict["rule"]


def test_fragile_parameters_are_named():
    solid = _sweep_table([1.0, 1.0, 1.0, 1.0], [0.1] * 4, parameter="solid")
    brittle = _sweep_table([1.0, 0.01, 0.01, 0.01], [0.1, 0.001, 0.001, 0.001],
                           parameter="brittle")
    table = pd.concat([solid, brittle], ignore_index=True)

    verdict = sensitivity.robustness_verdict(table)

    assert verdict["fragile_parameters"] == ["brittle"]


def test_an_empty_sweep_table_does_not_raise():
    verdict = sensitivity.robustness_verdict(pd.DataFrame())

    assert verdict["verdict"] == "no data"
