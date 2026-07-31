"""Tests for the indicator functions."""

import ast
import os

import numpy as np
import pandas as pd
import pytest

from trendfolge import indicators


def _index(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def test_sma_matches_hand_computation():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=_index(5))
    result = indicators.sma(series, 3)

    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_sma_warmup_length_is_exactly_window_minus_one():
    series = pd.Series(np.arange(50, dtype=float), index=_index(50))
    result = indicators.sma(series, 20)

    assert result.isna().sum() == 19
    assert not np.isnan(result.iloc[19])


def test_sma_rejects_non_positive_window():
    series = pd.Series([1.0, 2.0], index=_index(2))
    with pytest.raises(ValueError):
        indicators.sma(series, 0)


def test_true_range_first_bar_uses_own_span():
    high = pd.Series([10.0, 11.0], index=_index(2))
    low = pd.Series([9.0, 9.5], index=_index(2))
    close = pd.Series([9.5, 10.5], index=_index(2))

    tr = indicators.true_range(high, low, close)

    assert tr.iloc[0] == pytest.approx(1.0)
    assert tr.iloc[1] == pytest.approx(1.5)


def test_wilder_atr_matches_hand_computation():
    index = _index(5)
    high = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0], index=index)
    low = pd.Series([9.0, 9.5, 10.5, 10.0, 11.0], index=index)
    close = pd.Series([9.5, 10.5, 11.0, 10.5, 12.5], index=index)

    atr = indicators.wilder_atr(high, low, close, period=3)

    # True ranges are 1.0, 1.5, 1.5, 1.0, 2.5. The seed is their first-three
    # mean, then Wilder smoothing with alpha = 1/3.
    assert np.isnan(atr.iloc[0])
    assert np.isnan(atr.iloc[1])
    assert atr.iloc[2] == pytest.approx(4.0 / 3.0)
    assert atr.iloc[3] == pytest.approx((4.0 / 3.0 * 2 + 1.0) / 3.0)
    assert atr.iloc[4] == pytest.approx((((4.0 / 3.0 * 2 + 1.0) / 3.0) * 2 + 2.5) / 3.0)


def test_wilder_atr_is_all_nan_when_series_shorter_than_period():
    index = _index(3)
    frame = pd.Series([10.0, 11.0, 12.0], index=index)
    atr = indicators.wilder_atr(frame, frame - 1.0, frame, period=5)

    assert atr.isna().all()


def test_wilder_atr_rejects_short_period():
    index = _index(3)
    frame = pd.Series([10.0, 11.0, 12.0], index=index)
    with pytest.raises(ValueError):
        indicators.wilder_atr(frame, frame - 1.0, frame, period=1)


def test_is_rising_compares_against_own_past_and_is_false_when_unwarmed():
    series = pd.Series([1.0, 2.0, 3.0, 2.5], index=_index(4))

    rising = indicators.is_rising(series, lookback=2)

    assert not rising.iloc[0]
    assert not rising.iloc[1]
    assert rising.iloc[2]
    assert rising.iloc[3]

    falling = indicators.is_rising(pd.Series([3.0, 2.0, 1.0], index=_index(3)), lookback=2)
    assert not falling.iloc[2]


def test_average_traded_value_and_volume():
    index = _index(4)
    close = pd.Series([10.0, 10.0, 10.0, 20.0], index=index)
    volume = pd.Series([100.0, 200.0, 300.0, 400.0], index=index)

    value = indicators.average_traded_value(close, volume, window=2)
    shares = indicators.average_volume(volume, window=2)

    assert np.isnan(value.iloc[0])
    assert value.iloc[1] == pytest.approx((1000.0 + 2000.0) / 2)
    assert value.iloc[3] == pytest.approx((3000.0 + 8000.0) / 2)
    assert shares.iloc[3] == pytest.approx(350.0)


def test_rolling_extremes_are_trailing_and_inclusive():
    series = pd.Series([5.0, 1.0, 9.0, 3.0], index=_index(4))

    assert indicators.rolling_max(series, 3).iloc[2] == pytest.approx(9.0)
    assert indicators.rolling_min(series, 3).iloc[3] == pytest.approx(1.0)


def test_no_centred_rolling_window_anywhere_in_the_module():
    """A centred window reads the future. No call in this module may request one.

    The check walks the parsed syntax tree rather than the raw text, so it is
    not fooled by the word appearing in a comment or docstring, and it catches
    ``center=some_flag`` as well as the literal.
    """
    path = os.path.join(os.path.dirname(indicators.__file__), "indicators.py")
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "center"
    ]
    assert offenders == [], f"centred window requested at lines {offenders}"


def test_every_rolling_call_pins_min_periods_to_the_full_window():
    """A short window at the start of a series would emit half-warm values."""
    path = os.path.join(os.path.dirname(indicators.__file__), "indicators.py")
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    rolling_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rolling"
    ]
    assert rolling_calls, "expected this module to use rolling windows"
    for call in rolling_calls:
        supplied = {keyword.arg for keyword in call.keywords}
        assert "min_periods" in supplied, f"rolling() at line {call.lineno} omits min_periods"


def test_every_indicator_is_causal_under_future_mutation():
    """Changing bars after index k must not change any indicator value up to k."""
    index = _index(120)
    rng = np.random.default_rng(7)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))), index=index)
    high = close * 1.01
    low = close * 0.99

    cut = 80
    mutated_close = close.copy()
    mutated_close.iloc[cut:] *= 1.5
    mutated_high = mutated_close * 1.01
    mutated_low = mutated_close * 0.99

    for original, mutated in (
        (indicators.sma(close, 20), indicators.sma(mutated_close, 20)),
        (
            indicators.wilder_atr(high, low, close, 14),
            indicators.wilder_atr(mutated_high, mutated_low, mutated_close, 14),
        ),
        (
            indicators.average_traded_value(close, close * 0 + 1000.0, 20),
            indicators.average_traded_value(mutated_close, mutated_close * 0 + 1000.0, 20),
        ),
    ):
        pd.testing.assert_series_equal(original.iloc[:cut], mutated.iloc[:cut])
