"""Tests for exchange rate handling."""

import numpy as np
import pandas as pd
import pytest

from trendfolge.datasets.fx import FxError, FxRates, constant_fx, load_fx_rates
from trendfolge.universe import FxPair


def _index(start="2020-01-01", periods=10):
    return pd.bdate_range(start, periods=periods)


def test_account_currency_rate_is_always_one():
    index = _index()
    rates = FxRates("EUR", {"USD": pd.Series(0.9, index=index)}).align(index)

    assert rates.rate("EUR", index[0]) == 1.0
    assert rates.convert(250.0, "EUR", index[3]) == pytest.approx(250.0)


def test_conversion_multiplies_by_the_rate():
    index = _index()
    rates = FxRates("EUR", {"USD": pd.Series(0.8, index=index)}).align(index)

    assert rates.convert(100.0, "USD", index[2]) == pytest.approx(80.0)


def test_rates_are_forward_filled_across_a_gap():
    index = _index(periods=10)
    sparse = pd.Series([0.9, 0.95, 0.97], index=[index[0], index[5], index[9]])
    rates = FxRates("EUR", {"USD": sparse}).align(index)

    # Holding the last known spot rate over an equity market holiday is fair:
    # FX itself keeps trading. The gaps here sit strictly inside the series.
    assert rates.rate("USD", index[3]) == pytest.approx(0.9)
    assert rates.rate("USD", index[5]) == pytest.approx(0.95)
    assert rates.rate("USD", index[7]) == pytest.approx(0.95)
    assert rates.rate("USD", index[9]) == pytest.approx(0.97)


def test_rates_are_never_extrapolated_past_the_end_of_the_source():
    index = _index(periods=10)
    short = pd.Series([0.9, 0.91], index=index[:2])
    rates = FxRates("EUR", {"USD": short}).align(index)

    assert rates.rate("USD", index[1]) == pytest.approx(0.91)
    with pytest.raises(FxError, match="does not cover"):
        rates.rate("USD", index[5])


def test_missing_rate_before_the_series_starts_is_an_error():
    index = _index(periods=10)
    late = pd.Series([0.9], index=[index[5]])
    rates = FxRates("EUR", {"USD": late}).align(index)

    with pytest.raises(FxError):
        rates.rate("USD", index[0])


def test_unknown_currency_is_an_error():
    index = _index()
    rates = FxRates("EUR", {"USD": pd.Series(0.9, index=index)}).align(index)

    with pytest.raises(FxError, match="no exchange rate"):
        rates.rate("JPY", index[0])


def test_frame_access_before_align_is_an_error():
    index = _index()
    rates = FxRates("EUR", {"USD": pd.Series(0.9, index=index)})

    with pytest.raises(FxError, match="align"):
        _ = rates.frame


def test_non_positive_or_empty_series_are_rejected():
    index = _index()
    with pytest.raises(FxError, match="non-positive"):
        FxRates("EUR", {"USD": pd.Series(-1.0, index=index)})
    with pytest.raises(FxError, match="empty"):
        FxRates("EUR", {"USD": pd.Series(dtype=float)})


def test_load_fx_rates_inverts_when_asked(tmp_path):
    fx_dir = tmp_path / "fx"
    fx_dir.mkdir()
    (fx_dir / "EURUSD=X.csv").write_text(
        "Date,Open,High,Low,Close,Adj Close,Volume\n"
        "2020-01-02,1.10,1.11,1.09,1.25,1.25,0\n"
        "2020-01-03,1.25,1.26,1.24,1.25,1.25,0\n",
        encoding="utf-8",
    )

    index = pd.DatetimeIndex(["2020-01-02", "2020-01-03"])
    rates = load_fx_rates(str(tmp_path), [FxPair("USD", "EURUSD=X", invert=True)], "EUR")
    aligned = rates.align(index)

    # The file quotes USD per EUR; the engine needs EUR per USD.
    assert aligned.rate("USD", index[0]) == pytest.approx(1.0 / 1.25)


def test_load_fx_rates_without_inversion(tmp_path):
    fx_dir = tmp_path / "fx"
    fx_dir.mkdir()
    (fx_dir / "USDEUR.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2020-01-02,0.8,0.81,0.79,0.80,0\n",
        encoding="utf-8",
    )

    index = pd.DatetimeIndex(["2020-01-02"])
    rates = load_fx_rates(str(tmp_path), [FxPair("EUR2", "USDEUR")], "EUR").align(index)

    assert rates.rate("EUR2", index[0]) == pytest.approx(0.80)


def test_missing_fx_file_raises(tmp_path):
    (tmp_path / "fx").mkdir()
    with pytest.raises(FileNotFoundError, match="no FX file"):
        load_fx_rates(str(tmp_path), [FxPair("USD", "MISSING")], "EUR")


def test_constant_fx_helper_covers_every_requested_currency():
    index = _index()
    rates = constant_fx("EUR", ["USD", "SEK", "EUR"], index, rate=2.0)

    assert rates.rate("USD", index[0]) == 2.0
    assert rates.rate("SEK", index[0]) == 2.0
    assert rates.rate("EUR", index[0]) == 1.0


def test_align_is_idempotent_and_returns_a_new_object():
    index = _index()
    original = FxRates("EUR", {"USD": pd.Series(0.9, index=index)})
    first = original.align(index)
    second = first.align(index)

    assert first is not original
    np.testing.assert_allclose(
        first.series("USD").to_numpy(), second.series("USD").to_numpy()
    )
