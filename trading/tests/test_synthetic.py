"""Tests for the synthetic data generators.

These generators are the foundation every other test stands on, so their
determinism and their bar invariants are checked directly.
"""

import numpy as np
import pandas as pd
import pytest

from trendfolge import indicators
from trendfolge.datasets import synthetic


def test_same_seed_produces_identical_output():
    first = synthetic.make_gbm_close(seed=11, n=500)
    second = synthetic.make_gbm_close(seed=11, n=500)
    third = synthetic.make_gbm_close(seed=12, n=500)

    np.testing.assert_array_equal(first, second)
    assert not np.array_equal(first, third)


def test_generators_do_not_touch_the_global_rng():
    np.random.seed(0)
    before = np.random.random()

    synthetic.make_gbm_close(seed=3, n=100)
    synthetic.make_regime_close(seed=3, segments=[(50, 0.1, 0.2), (50, -0.2, 0.3)])

    np.random.seed(0)
    after = np.random.random()
    assert before == after


def test_ohlc_invariants_hold_on_a_long_noisy_series():
    index = synthetic.weekdays("2010-01-01", "2020-01-01")
    closes = synthetic.make_gbm_close(seed=5, n=len(index), sigma=0.45)
    frame = synthetic.make_ohlc_from_close(
        closes, index, range_frac=0.03, gap_frac=0.01, volume_noise=0.5, seed=5
    )

    assert (frame["high"] >= frame[["open", "close"]].max(axis=1) - 1e-12).all()
    assert (frame["low"] <= frame[["open", "close"]].min(axis=1) + 1e-12).all()
    assert (frame["high"] >= frame["low"]).all()
    assert (frame["close"] > 0).all()
    assert (frame["volume"] > 0).all()
    assert list(frame.columns) == ["open", "high", "low", "close", "adj_close", "volume"]


def test_ohlc_without_gaps_opens_at_the_previous_close():
    index = synthetic.weekdays("2020-01-01", "2020-03-01")
    closes = np.linspace(100.0, 110.0, len(index))
    frame = synthetic.make_ohlc_from_close(closes, index)

    np.testing.assert_allclose(frame["open"].to_numpy()[1:], closes[:-1])
    assert frame["open"].iloc[0] == pytest.approx(closes[0])


def test_ohlc_requires_a_seed_when_randomness_is_requested():
    index = synthetic.weekdays("2020-01-01", "2020-02-01")
    closes = np.linspace(100.0, 101.0, len(index))

    with pytest.raises(ValueError):
        synthetic.make_ohlc_from_close(closes, index, gap_frac=0.01)


def test_ohlc_rejects_length_mismatch():
    index = synthetic.weekdays("2020-01-01", "2020-02-01")
    with pytest.raises(ValueError):
        synthetic.make_ohlc_from_close(np.array([1.0, 2.0]), index)


def test_exchange_calendars_are_deliberately_not_aligned():
    nyse = synthetic.make_sessions("2015-01-01", "2020-12-31", "XNYS")
    xetr = synthetic.make_sessions("2015-01-01", "2020-12-31", "XETR")

    assert len(nyse) != len(xetr)
    assert len(nyse.difference(xetr)) > 0
    assert len(xetr.difference(nyse)) > 0
    # Both are weekday-only.
    assert (nyse.dayofweek < 5).all()
    assert (xetr.dayofweek < 5).all()


def test_unknown_exchange_falls_back_to_plain_weekdays():
    sessions = synthetic.make_sessions("2020-01-01", "2020-12-31", "XXXX")
    assert sessions.equals(synthetic.weekdays("2020-01-01", "2020-12-31"))


def test_scripted_pullback_is_deterministic_and_contains_exactly_one_setup():
    closes, reclaim_index = synthetic.make_scripted_pullback_close()
    again, _ = synthetic.make_scripted_pullback_close()
    np.testing.assert_array_equal(closes, again)

    index = synthetic.make_sessions("2005-01-03", "2030-01-01", "XNAS")[: len(closes)]
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.020)

    sma20 = indicators.sma(frame["close"], 20)
    sma50 = indicators.sma(frame["close"], 50)
    sma200 = indicators.sma(frame["close"], 200)
    atr = indicators.wilder_atr(frame["high"], frame["low"], frame["close"], 14)

    setups = []
    for position in range(220, len(frame)):
        close = frame["close"].iat[position]
        trend_ok = (
            close > sma200.iat[position]
            and sma200.iat[position] > sma200.iat[position - 20]
            and sma50.iat[position] > sma200.iat[position]
        )
        confirm_ok = (
            close > sma20.iat[position]
            and close > frame["high"].iat[position - 1]
            and close > frame["open"].iat[position]
        )
        window = slice(position - 5, position + 1)
        touched = bool((frame["low"].iloc[window] <= sma20.iloc[window]).any())
        deep_enough = (
            frame["low"].iloc[window].min()
            >= sma20.iat[position] - 1.5 * atr.iat[position]
        )
        if trend_ok and confirm_ok and touched and deep_enough:
            setups.append(position)

    assert setups == [reclaim_index]


def test_scripted_pullback_dip_actually_pierces_the_pullback_average():
    closes, reclaim_index = synthetic.make_scripted_pullback_close()
    index = synthetic.make_sessions("2005-01-03", "2030-01-01", "XNAS")[: len(closes)]
    frame = synthetic.make_ohlc_from_close(closes, index, range_frac=0.020)
    sma20 = indicators.sma(frame["close"], 20)

    dip = slice(reclaim_index - 3, reclaim_index)
    assert (frame["low"].iloc[dip] < sma20.iloc[dip]).any()
    # The reclaim bar closes back above the average, above the previous high and
    # above its own open. All three are required by the entry rule.
    assert frame["close"].iat[reclaim_index] > sma20.iat[reclaim_index]
    assert frame["close"].iat[reclaim_index] > frame["high"].iat[reclaim_index - 1]
    assert frame["close"].iat[reclaim_index] > frame["open"].iat[reclaim_index]


def test_market_factor_creates_both_bull_and_bear_phases():
    index = synthetic.weekdays("2005-01-03", "2026-06-30")
    closes = synthetic.make_regime_close(
        seed=42, segments=[(500, 0.20, 0.15), (200, -0.45, 0.40), (500, 0.20, 0.15)]
    )
    series = pd.Series(closes)
    peak = series.cummax()
    drawdown = (series / peak - 1.0).min()

    assert drawdown < -0.10
    assert series.iloc[-1] > series.iloc[0]


def test_make_universe_writes_all_files(tmp_path):
    manifest = synthetic.make_universe(
        str(tmp_path), seed=1, start="2015-01-01", end="2017-12-31"
    )

    ohlcv = tmp_path / "ohlcv"
    fx = tmp_path / "fx"
    assert len(list(ohlcv.glob("*.csv"))) == len(manifest["symbols"])
    assert len(list(fx.glob("*.csv"))) == 2

    sample = pd.read_csv(ohlcv / f"{manifest['benchmark']}.csv")
    assert list(sample.columns) == [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume",
    ]
    assert len(sample) > 500


def test_make_universe_symbols_have_different_session_counts(tmp_path):
    """Different exchanges must produce different numbers of bars."""
    synthetic.make_universe(str(tmp_path), seed=1, start="2015-01-01", end="2017-12-31")

    us = pd.read_csv(tmp_path / "ohlcv" / "SYN-US-A.csv")
    de = pd.read_csv(tmp_path / "ohlcv" / "SYN-EU-A.csv")
    assert len(us) != len(de)


def test_synthetic_universe_yaml_is_parseable_and_covers_every_currency():
    import yaml

    document = yaml.safe_load(synthetic.synthetic_universe_yaml())

    currencies = {entry["currency"] for entry in document["symbols"]}
    covered = {pair["currency"] for pair in document["fx_pairs"]} | {
        document["account_currency"]
    }
    assert currencies <= covered
