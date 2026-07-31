"""Tests for CSV loading, normalization and price adjustment."""

import numpy as np
import pandas as pd
import pytest

from trendfolge.datasets import loader, normalize
from trendfolge.datasets.normalize import DataValidationError


def _write(path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


YAHOO = """Date,Open,High,Low,Close,Adj Close,Volume
2020-01-02,100.0,102.0,99.0,101.0,50.5,1000000
2020-01-03,101.0,103.0,100.5,102.5,51.25,1100000
2020-01-06,102.5,104.0,102.0,103.0,51.5,900000
"""

STOOQ = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,102.0,99.0,101.0,1000000
2020-01-03,101.0,103.0,100.5,102.5,1100000
"""

TRADINGVIEW_ISO = """time,open,high,low,close,Volume
2020-01-02T00:00:00Z,100.0,102.0,99.0,101.0,1000000
2020-01-03T00:00:00Z,101.0,103.0,100.5,102.5,1100000
"""

TRADINGVIEW_UNIX = """time,open,high,low,close,Volume
1577923200,100.0,102.0,99.0,101.0,1000000
1578009600,101.0,103.0,100.5,102.5,1100000
"""


def test_detects_all_three_dialects(tmp_path):
    assert loader.detect_dialect(["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]) == "yahoo"
    assert loader.detect_dialect(["Date", "Open", "High", "Low", "Close", "Volume"]) == "stooq"
    assert loader.detect_dialect(["time", "open", "high", "low", "close", "Volume"]) == "tradingview"


def test_detect_dialect_rejects_incomplete_header():
    with pytest.raises(DataValidationError):
        loader.detect_dialect(["Date", "Open", "Close"])


def test_column_names_are_case_insensitive():
    assert loader.detect_dialect(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "ADJ CLOSE"]) == "yahoo"


def test_yahoo_file_loads_and_is_marked_adjusted(tmp_path):
    path = _write(tmp_path / "AAA.csv", YAHOO)
    data = loader.load_price_data(path, "AAA", price_mode="raw")

    assert data.source == "yahoo"
    assert data.is_adjusted
    assert list(data.frame.columns) == normalize.CANONICAL_COLUMNS
    assert len(data.frame) == 3
    assert data.frame.index[0] == pd.Timestamp("2020-01-02")


def test_stooq_file_without_adjusted_close_is_flagged(tmp_path):
    path = _write(tmp_path / "BBB.csv", STOOQ)
    data = loader.load_price_data(path, "BBB")

    assert data.source == "stooq"
    assert not data.is_adjusted
    # adj_close falls back to close, so the adjustment becomes a no-op.
    assert data.frame["adj_close"].equals(data.frame["close"])


def test_tradingview_iso_and_unix_timestamps_agree(tmp_path):
    iso = loader.load_price_data(_write(tmp_path / "C1.csv", TRADINGVIEW_ISO), "C1")
    unix = loader.load_price_data(_write(tmp_path / "C2.csv", TRADINGVIEW_UNIX), "C2")

    assert iso.source == "tradingview"
    assert unix.source == "tradingview"
    assert list(iso.frame.index) == list(unix.frame.index)


def test_unsorted_dates_are_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-03,101.0,103.0,100.5,102.5,1100000
2020-01-02,100.0,102.0,99.0,101.0,1000000
"""
    with pytest.raises(DataValidationError, match="ascending"):
        loader.load_price_data(_write(tmp_path / "D.csv", text), "D")


def test_duplicate_dates_are_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,102.0,99.0,101.0,1000000
2020-01-02,100.0,102.0,99.0,101.0,1000000
"""
    with pytest.raises(DataValidationError, match="duplicate"):
        loader.load_price_data(_write(tmp_path / "E.csv", text), "E")


def test_nan_close_is_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,102.0,99.0,,1000000
"""
    with pytest.raises(DataValidationError, match="NaN"):
        loader.load_price_data(_write(tmp_path / "F.csv", text), "F")


def test_high_below_close_is_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,100.5,99.0,101.0,1000000
"""
    with pytest.raises(DataValidationError, match="high below"):
        loader.load_price_data(_write(tmp_path / "G.csv", text), "G")


def test_low_above_open_is_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,102.0,100.5,101.0,1000000
"""
    with pytest.raises(DataValidationError, match="low above"):
        loader.load_price_data(_write(tmp_path / "H.csv", text), "H")


def test_non_positive_price_is_rejected(tmp_path):
    text = """Date,Open,High,Low,Close,Volume
2020-01-02,100.0,102.0,0.0,101.0,1000000
"""
    with pytest.raises(DataValidationError, match="non-positive"):
        loader.load_price_data(_write(tmp_path / "I.csv", text), "I")


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(DataValidationError):
        loader.load_price_data(_write(tmp_path / "J.csv", "Date,Open,High,Low,Close,Volume\n"), "J")


def test_adjusted_mode_rescales_the_whole_bar(tmp_path):
    path = _write(tmp_path / "K.csv", YAHOO)
    raw = loader.load_price_data(path, "K", price_mode="raw").frame
    adjusted = loader.load_price_data(path, "K", price_mode="adjusted").frame

    ratio = 50.5 / 101.0
    assert adjusted["close"].iloc[0] == pytest.approx(50.5)
    assert adjusted["open"].iloc[0] == pytest.approx(100.0 * ratio)
    assert adjusted["high"].iloc[0] == pytest.approx(102.0 * ratio)
    assert adjusted["low"].iloc[0] == pytest.approx(99.0 * ratio)
    # The bar geometry survives the rescaling.
    assert adjusted["high"].iloc[0] >= adjusted["close"].iloc[0]
    # Raw mode leaves the prices alone.
    assert raw["close"].iloc[0] == pytest.approx(101.0)


def test_adjustment_preserves_relative_moves():
    """A split-adjusted series must have the same bar-to-bar returns as raw."""
    index = pd.bdate_range("2020-01-01", periods=10)
    close = pd.Series(np.linspace(100.0, 120.0, 10), index=index)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close * 0.5,
            "volume": 1000.0,
        }
    )
    adjusted = normalize.apply_price_mode(frame, "adjusted", "L")

    pd.testing.assert_series_equal(
        adjusted["close"].pct_change().iloc[1:],
        frame["close"].pct_change().iloc[1:],
        check_names=False,
    )


def test_unknown_price_mode_is_rejected():
    index = pd.bdate_range("2020-01-01", periods=3)
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "adj_close": 100.0,
            "volume": 10.0,
        },
        index=index,
    )
    with pytest.raises(DataValidationError):
        normalize.apply_price_mode(frame, "sideways", "M")


def test_missing_file_raises_a_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="NOPE"):
        loader.find_price_file(str(tmp_path), "NOPE")


def test_date_slicing_bounds_are_inclusive(tmp_path):
    path = _write(tmp_path / "N.csv", YAHOO)
    data = loader.load_price_data(path, "N", start="2020-01-03", end="2020-01-06")

    assert len(data.frame) == 2
    assert data.first_date == pd.Timestamp("2020-01-03")
    assert data.last_date == pd.Timestamp("2020-01-06")


def test_slicing_to_an_empty_range_raises(tmp_path):
    path = _write(tmp_path / "O.csv", YAHOO)
    with pytest.raises(DataValidationError, match="no bars left"):
        loader.load_price_data(path, "O", start="2021-01-01")
