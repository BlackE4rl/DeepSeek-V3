"""Tests for the entry and exit rules.

The scripted pullback generator is deterministic and its shape is known in
advance, so these tests assert that the signal fires on exactly the intended bar
and on no other -- not merely that "a signal happened somewhere".
"""

import numpy as np
import pandas as pd
import pytest

from helpers import pullback_frame, ramp_frame
from trendfolge import indicators, strategy
from trendfolge.config import StrategyParams
from trendfolge.datasets import synthetic

SESSIONS = synthetic.make_sessions("2005-01-03", "2035-01-01", "XNAS")


def _sessions(n: int) -> pd.DatetimeIndex:
    return SESSIONS[:n]


def test_entry_fires_on_exactly_the_confirmation_bar():
    frame, reclaim = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)
    fired = np.flatnonzero(signals["entry_raw"].to_numpy())

    assert list(fired) == [reclaim]


def test_entry_does_not_fire_one_bar_early_or_late():
    frame, reclaim = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)
    signals = strategy.compute_signals(frame, params)

    assert not signals["entry_raw"].iloc[reclaim - 1]
    assert signals["entry_raw"].iloc[reclaim]
    assert not signals["entry_raw"].iloc[reclaim + 1]


def test_dip_bars_are_blocked_by_the_confirmation_conditions():
    """While the dip is still in progress the bar is down and below the prior high."""
    frame, reclaim = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)
    signals = strategy.compute_signals(frame, params)

    for position in range(reclaim - 3, reclaim):
        assert not signals["confirm"].iloc[position]


def test_a_pure_uptrend_without_a_pullback_never_triggers():
    frame = ramp_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)

    assert signals["trend_ok"].iloc[300:].all()
    assert not signals["touched"].iloc[220:].any()
    assert not signals["entry_raw"].any()


def test_a_downtrend_fails_the_trend_filter():
    index = _sessions(400)
    closes = 200.0 * np.cumprod(np.r_[1.0, np.full(len(index) - 1, 0.9985)])
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.020)
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)

    assert not signals["trend_ok"].iloc[220:].any()
    assert not signals["entry_raw"].any()


def test_a_collapse_is_not_a_pullback():
    """A dip deeper than the ATR limit must be rejected even though it touched."""
    frame, reclaim = pullback_frame(
        _sessions(400), dip_bars=6, dip_per_bar=-0.030, reclaim=0.035
    )
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)

    assert signals["touched"].iloc[reclaim]
    assert not signals["depth_ok"].iloc[reclaim]
    assert not signals["entry_raw"].any()


def test_a_touch_older_than_the_window_no_longer_counts():
    frame, reclaim = pullback_frame(_sessions(400))
    narrow = strategy.compute_signals(
        frame, StrategyParams(min_adv_acct=0.0, pullback_touch_window=1)
    )["touched"]
    wide = strategy.compute_signals(
        frame, StrategyParams(min_adv_acct=0.0, pullback_touch_window=5)
    )["touched"]

    # Two bars after the reclaim the price is back above the average, so a
    # one-bar window sees nothing while a five-bar window still remembers the dip.
    assert not narrow.iloc[reclaim + 2]
    assert wide.iloc[reclaim + 2]

    # Far enough past the dip, even the five-bar window forgets it.
    assert not wide.iloc[reclaim + 8]


def test_the_slope_filter_actually_binds_on_a_realistic_series():
    """There must be bars the slope gate rejects that the other trend gates accept."""
    index = _sessions(3000)
    closes = synthetic.make_gbm_close(seed=17, n=len(index), mu=0.08, sigma=0.30)
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.02)
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)
    sma_long = signals["sma_long"]
    sma_mid = signals["sma_mid"]
    without_slope = (frame["close"] > sma_long) & (sma_mid > sma_long)
    rising = indicators.is_rising(sma_long, params.sma_slope_lookback)

    blocked = without_slope.fillna(False) & ~rising
    assert blocked.any(), "expected the slope gate to reject some bars"
    assert not signals.loc[blocked, "trend_ok"].any()


def test_warmup_blocks_signals_from_half_warm_indicators():
    frame, _reclaim = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)

    assert not signals["warm"].iloc[: params.warmup_bars].any()
    assert signals["warm"].iloc[params.warmup_bars :].all()
    assert not signals["entry_raw"].iloc[: params.warmup_bars].any()


def test_liquidity_filter_blocks_a_thin_symbol():
    frame, reclaim = pullback_frame(_sessions(400), base_volume=1000.0)
    params = StrategyParams(min_adv_acct=20_000_000.0)

    signals = strategy.compute_signals(frame, params)

    assert not signals["liquidity_ok"].iloc[reclaim]
    assert not signals["entry_raw"].any()


def test_liquidity_filter_is_measured_in_the_account_currency():
    """A weak local currency can push an otherwise liquid symbol below the floor."""
    frame, reclaim = pullback_frame(_sessions(400), base_volume=500_000.0)
    params = StrategyParams(min_adv_acct=20_000_000.0)

    strong = pd.Series(1.0, index=frame.index)
    weak = pd.Series(0.10, index=frame.index)

    assert strategy.compute_signals(frame, params, strong)["liquidity_ok"].iloc[reclaim]
    assert not strategy.compute_signals(frame, params, weak)["liquidity_ok"].iloc[reclaim]


def test_minimum_price_filter_blocks_penny_prices():
    frame, reclaim = pullback_frame(_sessions(400), start_price=1.0)
    params = StrategyParams(min_adv_acct=0.0, min_price_local=5.0)

    signals = strategy.compute_signals(frame, params)

    assert not signals["liquidity_ok"].iloc[reclaim]


def test_rank_score_is_trend_extension_per_unit_of_volatility():
    frame, reclaim = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)
    signals = strategy.compute_signals(frame, params)

    close = frame["close"].iloc[reclaim]
    expected = (close / signals["sma_long"].iloc[reclaim] - 1.0) / (
        signals["atr"].iloc[reclaim] / close
    )
    assert signals["rank_score"].iloc[reclaim] == pytest.approx(expected)


def test_signal_generation_is_deterministic():
    frame, _ = pullback_frame(_sessions(400))
    params = StrategyParams(min_adv_acct=0.0)

    first = strategy.compute_signals(frame, params)
    second = strategy.compute_signals(frame, params)

    pd.testing.assert_frame_equal(first, second)


def test_signals_are_causal_under_future_mutation():
    """Rewriting the future must not change any past signal."""
    index = _sessions(1200)
    closes = synthetic.make_gbm_close(seed=3, n=len(index), mu=0.12, sigma=0.28)
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.02)
    params = StrategyParams(min_adv_acct=0.0)

    cut = 800
    mutated_closes = closes.copy()
    mutated_closes[cut:] *= 2.0
    mutated = synthetic.make_ohlc_from_close(mutated_closes, index, range_frac=0.02)

    original_signals = strategy.compute_signals(frame, params)
    mutated_signals = strategy.compute_signals(mutated, params)

    # The bar at `cut` itself is allowed to differ, because its own close moved.
    pd.testing.assert_frame_equal(
        original_signals.iloc[:cut], mutated_signals.iloc[:cut]
    )


def test_below_long_marks_the_trend_break_exit():
    index = _sessions(400)
    closes = np.r_[
        100.0 * np.cumprod(np.full(300, 1.0015)),
        100.0 * np.cumprod(np.full(300, 1.0015))[-1] * np.cumprod(np.full(100, 0.985)),
    ]
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.02)
    params = StrategyParams(min_adv_acct=0.0)

    signals = strategy.compute_signals(frame, params)

    assert not signals["below_long"].iloc[299]
    assert signals["below_long"].iloc[-1]


def test_regime_flag_follows_the_regime_instruments_long_average():
    index = _sessions(600)
    rising = ramp_frame(index, daily_growth=0.0012, range_frac=0.006)
    params = StrategyParams()

    regime = strategy.compute_regime(rising, params)

    # The 200-bar average is NaN until bar 199, and an unwarmed average must
    # read as "regime off" rather than as "regime on".
    assert not regime.iloc[:199].any()
    assert regime.iloc[250:].all()


def test_regime_turns_off_in_a_bear_market():
    index = _sessions(600)
    closes = np.r_[
        100.0 * np.cumprod(np.full(400, 1.0015)),
        100.0 * np.cumprod(np.full(400, 1.0015))[-1] * np.cumprod(np.full(200, 0.990)),
    ]
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.006)

    regime = strategy.compute_regime(frame, StrategyParams())

    assert regime.iloc[399]
    assert not regime.iloc[-1]


def test_initial_stop_is_anchored_to_the_fill_not_to_the_signal_close():
    params = StrategyParams(atr_stop_mult=2.5)
    assert strategy.initial_stop(100.0, 4.0, params) == pytest.approx(90.0)


def test_trailing_stop_ratchets_upwards_only():
    params = StrategyParams(atr_trail_mult=3.0)

    raised = strategy.trailing_stop(90.0, highest_close=110.0, atr_now=5.0, params=params)
    assert raised == pytest.approx(95.0)

    # A wider ATR would imply a lower stop; the stop must not follow it down.
    held = strategy.trailing_stop(95.0, highest_close=110.0, atr_now=9.0, params=params)
    assert held == pytest.approx(95.0)


def test_trailing_stop_ignores_a_missing_atr():
    params = StrategyParams()
    assert strategy.trailing_stop(90.0, 110.0, float("nan"), params) == pytest.approx(90.0)
