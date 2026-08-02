"""Tests for exposure matching, the common window and benchmark metrics.

These exist because an unfair benchmark is worse than no benchmark: it produces
a confident answer to a question nobody asked.
"""

import numpy as np
import pandas as pd
import pytest

from trendfolge import benchmarks


def _curve(values, start="2020-01-01"):
    index = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=index)


def _growing(n=500, annual=0.10, start="2020-01-01", value=100_000.0):
    index = pd.bdate_range(start, periods=n)
    return pd.Series(
        value * np.cumprod(np.full(n, (1.0 + annual) ** (1 / 252))), index=index
    )


# --- exposure matching -------------------------------------------------------


def test_full_weight_reproduces_the_benchmark():
    """At full weight the blend is the benchmark, restated from the same start.

    The absolute levels differ only because the blended curve is rebased to the
    supplied starting capital, so the comparison is on the return path.
    """
    curve = _growing()

    matched = benchmarks.exposure_matched(curve, 1.0, 100_000.0)

    assert matched.iloc[0] == pytest.approx(100_000.0)
    np.testing.assert_allclose(
        matched.pct_change().dropna().to_numpy(),
        curve.pct_change().dropna().to_numpy(),
        rtol=1e-12,
    )
    assert matched.iloc[-1] / matched.iloc[0] == pytest.approx(
        curve.iloc[-1] / curve.iloc[0], rel=1e-12
    )


def test_zero_weight_earns_only_the_risk_free_rate():
    curve = _growing(n=253)

    matched = benchmarks.exposure_matched(curve, 0.0, 100_000.0, risk_free_rate=0.02)

    assert matched.iloc[-1] == pytest.approx(100_000.0 * (1 + 0.02 / 252) ** 252, rel=1e-6)


def test_zero_weight_and_no_interest_stays_flat():
    curve = _growing()

    matched = benchmarks.exposure_matched(curve, 0.0, 100_000.0, risk_free_rate=0.0)

    assert matched.nunique() == 1
    assert matched.iloc[-1] == pytest.approx(100_000.0)


def test_partial_weight_sits_between_the_two_extremes():
    curve = _growing()

    half = benchmarks.exposure_matched(curve, 0.5, 100_000.0)

    assert 100_000.0 < half.iloc[-1] < curve.iloc[-1]


def test_matching_exposure_scales_down_the_volatility():
    """The whole point: compare like risk with like risk."""
    rng = np.random.default_rng(2)
    index = pd.bdate_range("2020-01-01", periods=800)
    curve = pd.Series(100_000.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, 800))), index=index)

    matched = benchmarks.exposure_matched(curve, 0.3, 100_000.0)

    assert matched.pct_change().std() == pytest.approx(
        0.3 * curve.pct_change().std(), rel=1e-6
    )


def test_weights_outside_zero_and_one_are_clamped():
    curve = _growing()

    assert benchmarks.exposure_matched(curve, 5.0, 100_000.0).iloc[-1] == pytest.approx(
        benchmarks.exposure_matched(curve, 1.0, 100_000.0).iloc[-1]
    )
    assert benchmarks.exposure_matched(curve, -3.0, 100_000.0).iloc[-1] == pytest.approx(
        100_000.0
    )


def test_exposure_matching_an_empty_curve_returns_empty():
    assert benchmarks.exposure_matched(pd.Series(dtype=float), 0.5, 100_000.0).empty


# --- common window -----------------------------------------------------------


def test_common_window_is_the_overlap_of_every_curve():
    long_curve = _growing(n=500, start="2015-01-01")
    short_curve = _growing(n=200, start="2016-01-01")

    window = benchmarks.common_window({"long": long_curve, "short": short_curve})

    assert window[0] == short_curve.index[0]
    assert window[1] == short_curve.index[-1]


def test_common_window_ignores_leading_and_trailing_nan():
    base = _growing(n=300)
    padded = base.copy()
    padded.iloc[:50] = np.nan

    window = benchmarks.common_window({"a": base, "b": padded})

    assert window[0] == base.index[50]


def test_disjoint_curves_have_no_common_window():
    first = _growing(n=100, start="2015-01-01")
    second = _growing(n=100, start="2020-01-01")

    assert benchmarks.common_window({"a": first, "b": second}) is None


def test_common_window_of_nothing_is_none():
    assert benchmarks.common_window({}) is None
    assert benchmarks.common_window({"a": pd.Series(dtype=float)}) is None


# --- rebasing ----------------------------------------------------------------


def test_rebasing_restates_a_curve_from_a_common_start():
    curve = _growing(n=400)
    start, end = curve.index[100], curve.index[300]

    rebased = benchmarks.rebase(curve, start, end, 50_000.0)

    assert rebased.iloc[0] == pytest.approx(50_000.0)
    assert rebased.index[0] == start
    assert rebased.index[-1] == end
    # Growth over the window is preserved exactly.
    assert rebased.iloc[-1] / rebased.iloc[0] == pytest.approx(
        curve.loc[end] / curve.loc[start]
    )


def test_rebasing_outside_the_data_yields_an_empty_series():
    curve = _growing(n=100)

    empty = benchmarks.rebase(
        curve, pd.Timestamp("2030-01-01"), pd.Timestamp("2031-01-01"), 100_000.0
    )

    assert empty.empty


# --- benchmark metrics -------------------------------------------------------


def test_curve_metrics_describe_a_fully_invested_position():
    # A curve that exactly doubles over two calendar years.
    index = pd.bdate_range("2021-01-01", "2022-12-31")
    span = (index[-1] - index[0]).days / 365.25
    curve = pd.Series(
        100_000.0 * np.linspace(1.0, 2.0, len(index)) ** 1.0, index=index
    )

    metrics = benchmarks.curve_metrics(curve)

    assert metrics["cagr"] == pytest.approx(2.0 ** (1 / span) - 1.0, rel=1e-9)
    assert metrics["days_with_a_position"] == pytest.approx(1.0)
    assert metrics["avg_invested_fraction"] == pytest.approx(1.0)
    assert metrics["n_trades"] == 0


def test_curve_metrics_of_an_empty_curve_is_empty():
    assert benchmarks.curve_metrics(pd.Series(dtype=float)) == {}
