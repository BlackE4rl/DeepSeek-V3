"""Technical indicators.

Every function here is strictly causal: the value at index i is computed only
from data at indices <= i. No rolling window in this module is centred, and a
test asserts that ``center=True`` never appears in this file, because a centred
window is the single easiest way to leak the future into a backtest.

All functions operate on a symbol's own native bar series, never on a series
that has been reindexed onto a shared multi-exchange calendar. Forward-filled
bars would silently deflate the ATR and flatten the moving average slope.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average.

    Args:
        series: Input series.
        window: Number of bars to average over.

    Returns:
        A series of the same index, NaN for the first ``window - 1`` bars.
    """
    if window < 1:
        raise ValueError(f"window must be at least 1, got {window}")
    return series.rolling(window=window, min_periods=window).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder's true range.

    The first bar has no previous close, so its true range is simply its own
    high-low span.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.

    Returns:
        The true range series.
    """
    previous_close = close.shift(1)
    spans = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    )
    result = spans.max(axis=1)
    result.iloc[0] = high.iloc[0] - low.iloc[0]
    return result


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average true range using Wilder's smoothing.

    The series is seeded with the arithmetic mean of the first ``period`` true
    ranges and then smoothed recursively as
    ``atr[t] = (atr[t-1] * (period - 1) + tr[t]) / period``. Values before the
    seed are NaN, so a strategy cannot act on a half-warm ATR.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: Smoothing period.

    Returns:
        The ATR series, NaN for the first ``period - 1`` bars.
    """
    if period < 2:
        raise ValueError(f"period must be at least 2, got {period}")
    tr = true_range(high, low, close).to_numpy(dtype=float)
    n = tr.size
    atr = np.full(n, np.nan, dtype=float)
    if n < period:
        return pd.Series(atr, index=high.index)

    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return pd.Series(atr, index=high.index)


def rolling_max(series: pd.Series, window: int) -> pd.Series:
    """Trailing maximum over ``window`` bars, inclusive of the current bar."""
    return series.rolling(window=window, min_periods=window).max()


def rolling_min(series: pd.Series, window: int) -> pd.Series:
    """Trailing minimum over ``window`` bars, inclusive of the current bar."""
    return series.rolling(window=window, min_periods=window).min()


def average_traded_value(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    """Average traded value per bar, in the instrument's own currency.

    Args:
        close: Close prices.
        volume: Share volume.
        window: Averaging window.

    Returns:
        The rolling mean of ``close * volume``.
    """
    return (close * volume).rolling(window=window, min_periods=window).mean()


def average_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Average share volume per bar, used for the participation cap."""
    return volume.rolling(window=window, min_periods=window).mean()


def is_rising(series: pd.Series, lookback: int) -> pd.Series:
    """Whether a series exceeds its own value ``lookback`` bars ago.

    Args:
        series: Input series, typically a long moving average.
        lookback: How far back to compare.

    Returns:
        A boolean series. Bars where either value is NaN yield False, so an
        unwarmed indicator never reads as a rising trend.
    """
    shifted = series.shift(lookback)
    return (series > shifted).fillna(False)
