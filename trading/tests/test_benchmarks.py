"""Tests for the benchmark constructions."""

import numpy as np
import pandas as pd
import pytest

from helpers import default_test_config, flat_frame, make_panel, make_universe, ramp_frame
from trendfolge import benchmarks
from trendfolge.config import CostParams
from trendfolge.costs import CostModel
from trendfolge.datasets import synthetic
from trendfolge.universe import SymbolMeta

SESSIONS = synthetic.make_sessions("2005-01-03", "2035-01-01", "XNAS")


def _panel(periods: int = 400):
    index = SESSIONS[:periods]
    frames = {
        "AAA": ramp_frame(index, start_price=100.0, daily_growth=0.0010),
        "BBB": ramp_frame(index, start_price=50.0, daily_growth=0.0005),
        "REGIME": flat_frame(index, price=100.0),
    }
    universe = make_universe(
        [SymbolMeta("AAA", "EUR", "XNAS", "A"), SymbolMeta("BBB", "EUR", "XNAS", "B")]
    )
    return make_panel(frames, universe, default_test_config()), frames


def test_buy_and_hold_tracks_the_instrument_without_costs():
    panel, frames = _panel()
    free = CostModel(CostParams.zero())

    curve = benchmarks.buy_and_hold(panel, "AAA", 100_000.0, free)
    instrument_return = frames["AAA"]["close"].iloc[-1] / frames["AAA"]["open"].iloc[0] - 1.0
    achieved = curve.iloc[-1] / 100_000.0 - 1.0

    # Only the leftover cash from whole-share rounding separates the two.
    assert achieved == pytest.approx(instrument_return, rel=0.001)


def test_buy_and_hold_pays_the_cost_model():
    panel, _frames = _panel()
    free = CostModel(CostParams.zero())
    costed = CostModel(CostParams(slippage_bps=5.0, half_spread_bps=3.0,
                                  commission_fixed_acct=1.0, min_commission_acct=1.0))

    assert (
        benchmarks.buy_and_hold(panel, "AAA", 100_000.0, costed).iloc[-1]
        < benchmarks.buy_and_hold(panel, "AAA", 100_000.0, free).iloc[-1]
    )


def test_buy_and_hold_of_an_unknown_symbol_is_all_nan():
    panel, _frames = _panel()

    curve = benchmarks.buy_and_hold(panel, "NOPE", 100_000.0, CostModel(CostParams.zero()))

    assert curve.isna().all()


def test_equal_weight_starts_at_the_initial_equity_and_holds_both_symbols():
    panel, _frames = _panel()
    free = CostModel(CostParams.zero())

    curve = benchmarks.equal_weight_universe(panel, 100_000.0, free)

    assert curve.iloc[0] == pytest.approx(100_000.0, rel=0.02)
    assert len(curve) == len(panel.master_index)
    assert curve.iloc[-1] > curve.iloc[0]


def test_equal_weight_sits_between_its_two_constituents():
    panel, _frames = _panel()
    free = CostModel(CostParams.zero())

    basket = benchmarks.equal_weight_universe(panel, 100_000.0, free).iloc[-1]
    fast = benchmarks.buy_and_hold(panel, "AAA", 100_000.0, free).iloc[-1]
    slow = benchmarks.buy_and_hold(panel, "BBB", 100_000.0, free).iloc[-1]

    assert slow < basket < fast


def test_equal_weight_rebalancing_is_not_free():
    """A frictionless benchmark would flatter every strategy compared against it."""
    panel, _frames = _panel()
    free = CostModel(CostParams.zero())
    costed = CostModel(
        CostParams(slippage_bps=20.0, half_spread_bps=20.0, commission_fixed_acct=5.0,
                   min_commission_acct=5.0)
    )

    assert (
        benchmarks.equal_weight_universe(panel, 100_000.0, costed).iloc[-1]
        < benchmarks.equal_weight_universe(panel, 100_000.0, free).iloc[-1]
    )


def test_relative_statistics_of_a_series_against_itself():
    index = pd.bdate_range("2020-01-01", periods=500)
    rng = np.random.default_rng(3)
    curve = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, 500))), index=index)

    stats = benchmarks.relative_statistics(curve, curve)

    assert stats["beta"] == pytest.approx(1.0)
    assert stats["correlation"] == pytest.approx(1.0)
    assert stats["alpha_annual"] == pytest.approx(0.0, abs=1e-9)
    assert stats["up_capture"] == pytest.approx(1.0)
    assert stats["down_capture"] == pytest.approx(1.0)


def test_a_geared_series_has_the_expected_beta():
    index = pd.bdate_range("2020-01-01", periods=800)
    rng = np.random.default_rng(5)
    benchmark_returns = rng.normal(0.0003, 0.01, 800)
    strategy_returns = 2.0 * benchmark_returns

    benchmark = pd.Series(100.0 * np.exp(np.cumsum(benchmark_returns)), index=index)
    strategy = pd.Series(100.0 * np.exp(np.cumsum(strategy_returns)), index=index)

    stats = benchmarks.relative_statistics(strategy, benchmark)

    assert stats["beta"] == pytest.approx(2.0, rel=0.05)
    assert stats["up_capture"] > 1.5
    assert stats["down_capture"] > 1.5


def test_relative_statistics_need_enough_overlap():
    index = pd.bdate_range("2020-01-01", periods=10)
    curve = pd.Series(np.linspace(100.0, 110.0, 10), index=index)

    stats = benchmarks.relative_statistics(curve, curve)

    assert np.isnan(stats["beta"])
