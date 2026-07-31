"""The canonical price frame, its validation and its split/dividend adjustment.

Bad input data is the single most common cause of backtest results that look
wonderful and are not real. Everything here therefore fails loudly: a frame that
does not satisfy the invariants raises rather than being silently repaired.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]

# Relative tolerance for the bar geometry checks. Vendor files are rounded to a
# few decimals, which can make a high sit a hair below a close that was rounded
# up. Anything larger than this is a genuine data error.
_GEOMETRY_TOLERANCE = 1e-6


class DataValidationError(ValueError):
    """Raised when a price frame violates one of the canonical invariants."""


@dataclass(frozen=True)
class PriceData:
    """A validated price series together with its provenance.

    Attributes:
        symbol: Ticker the frame belongs to.
        frame: Canonical OHLCV frame, indexed by session date.
        is_adjusted: Whether the source supplied a genuine adjusted close. False
            means splits and dividends are not accounted for, which the report
            has to say out loud.
        source: Which CSV dialect the data came from.
        path: Where it was read from.
    """

    symbol: str
    frame: pd.DataFrame
    is_adjusted: bool
    source: str
    path: str = ""

    @property
    def first_date(self) -> pd.Timestamp:
        """First session in the frame."""
        return self.frame.index[0]

    @property
    def last_date(self) -> pd.Timestamp:
        """Last session in the frame."""
        return self.frame.index[-1]


def normalize_index(index: pd.Index, symbol: str) -> pd.DatetimeIndex:
    """Coerce an index to tz-naive midnight timestamps and verify its ordering.

    Args:
        index: The raw index read from file.
        symbol: Ticker, used in error messages.

    Returns:
        A tz-naive DatetimeIndex normalized to midnight.

    Raises:
        DataValidationError: If the index contains duplicates or is not sorted.
    """
    converted = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce", utc=False))
    if converted.isna().any():
        bad = [str(raw) for raw, ok in zip(index, converted.isna()) if ok][:5]
        raise DataValidationError(f"{symbol}: unparseable dates, first offenders {bad}")

    if converted.tz is not None:
        converted = converted.tz_convert(None)
    converted = converted.normalize()

    duplicated = converted[converted.duplicated()]
    if len(duplicated):
        raise DataValidationError(
            f"{symbol}: duplicate dates in the price file: "
            f"{[str(d.date()) for d in duplicated[:5]]}"
        )
    if not converted.is_monotonic_increasing:
        positions = np.flatnonzero(converted.to_numpy()[1:] <= converted.to_numpy()[:-1])
        offenders = [str(converted[p + 1].date()) for p in positions[:5]]
        raise DataValidationError(
            f"{symbol}: dates are not in ascending order, first offenders {offenders}"
        )
    return converted


def validate_frame(frame: pd.DataFrame, symbol: str) -> None:
    """Check the canonical invariants of a price frame.

    Args:
        frame: Canonical OHLCV frame.
        symbol: Ticker, used in error messages.

    Raises:
        DataValidationError: If any invariant is violated, naming the dates.
    """
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise DataValidationError(f"{symbol}: frame is missing columns {missing}")

    if frame.empty:
        raise DataValidationError(f"{symbol}: price frame is empty")

    price_columns = ["open", "high", "low", "close", "adj_close"]
    nan_mask = frame[price_columns].isna().any(axis=1)
    if nan_mask.any():
        raise DataValidationError(
            f"{symbol}: NaN prices on {_format_dates(frame.index[nan_mask])}"
        )

    non_positive = (frame[price_columns] <= 0).any(axis=1)
    if non_positive.any():
        raise DataValidationError(
            f"{symbol}: non-positive prices on {_format_dates(frame.index[non_positive])}"
        )

    upper = frame[["open", "close"]].max(axis=1)
    lower = frame[["open", "close"]].min(axis=1)
    high_violation = frame["high"] < upper * (1.0 - _GEOMETRY_TOLERANCE)
    if high_violation.any():
        raise DataValidationError(
            f"{symbol}: high below open/close on {_format_dates(frame.index[high_violation])}"
        )
    low_violation = frame["low"] > lower * (1.0 + _GEOMETRY_TOLERANCE)
    if low_violation.any():
        raise DataValidationError(
            f"{symbol}: low above open/close on {_format_dates(frame.index[low_violation])}"
        )
    inverted = frame["high"] < frame["low"] * (1.0 - _GEOMETRY_TOLERANCE)
    if inverted.any():
        raise DataValidationError(
            f"{symbol}: high below low on {_format_dates(frame.index[inverted])}"
        )

    if (frame["volume"] < 0).any():
        raise DataValidationError(
            f"{symbol}: negative volume on "
            f"{_format_dates(frame.index[frame['volume'] < 0])}"
        )


def to_canonical(
    frame: pd.DataFrame, symbol: str, has_adjusted: bool
) -> pd.DataFrame:
    """Coerce a raw frame into the canonical column set, dtypes and ordering.

    Args:
        frame: Raw frame with canonical column names already applied.
        symbol: Ticker, used in error messages.
        has_adjusted: Whether the source supplied an adjusted close column.

    Returns:
        The canonical frame.
    """
    result = frame.copy()
    result.index = normalize_index(result.index, symbol)

    if not has_adjusted or "adj_close" not in result.columns:
        result["adj_close"] = result["close"]
    if "volume" not in result.columns:
        result["volume"] = 0.0

    result = result[CANONICAL_COLUMNS]
    result = result.astype("float64")
    result["volume"] = result["volume"].fillna(0.0)

    validate_frame(result, symbol)
    return result


def apply_price_mode(frame: pd.DataFrame, mode: str, symbol: str) -> pd.DataFrame:
    """Apply the requested split and dividend handling.

    In ``adjusted`` mode the whole bar is scaled by ``adj_close / close``, so the
    open, high and low move with the close. Without this, a four-for-one split
    looks like a seventy-five percent crash to a moving average and every
    ex-dividend date fires a phantom stop.

    The known cost of adjusting is that a dividend paid in the future
    retroactively rescales past prices, so absolute-level filters and ATR
    expressed in currency units carry a mild contamination. Every rule in this
    strategy is either a ratio or a comparison within the same price space, so
    signal ordering is unaffected -- but the ``raw`` mode exists so that claim
    can be checked rather than believed.

    Args:
        frame: Canonical frame.
        mode: Either ``adjusted`` or ``raw``.
        symbol: Ticker, used in error messages.

    Returns:
        A new frame in the requested price space.

    Raises:
        DataValidationError: If the adjustment ratio is not finite and positive.
    """
    if mode == "raw":
        result = frame.copy()
        result["adj_close"] = result["close"]
        return result
    if mode != "adjusted":
        raise DataValidationError(f"unknown price_mode {mode!r}")

    ratio = frame["adj_close"] / frame["close"]
    if not np.isfinite(ratio).all() or (ratio <= 0).any():
        bad = frame.index[~np.isfinite(ratio) | (ratio <= 0)]
        raise DataValidationError(
            f"{symbol}: invalid adjustment ratio on {_format_dates(bad)}"
        )

    result = frame.copy()
    for column in ("open", "high", "low"):
        result[column] = frame[column] * ratio
    result["close"] = frame["adj_close"]
    result["adj_close"] = frame["adj_close"]

    validate_frame(result, symbol)
    return result


def slice_dates(
    frame: pd.DataFrame, start: Optional[str], end: Optional[str]
) -> pd.DataFrame:
    """Restrict a frame to a date range.

    Args:
        frame: Canonical frame.
        start: Inclusive lower bound, ISO format, or None.
        end: Inclusive upper bound, ISO format, or None.

    Returns:
        The restricted frame.
    """
    result = frame
    if start is not None:
        result = result[result.index >= pd.Timestamp(start)]
    if end is not None:
        result = result[result.index <= pd.Timestamp(end)]
    return result


def _format_dates(index: pd.Index, limit: int = 5) -> str:
    """Render up to ``limit`` dates for an error message."""
    shown: List[str] = [str(pd.Timestamp(value).date()) for value in index[:limit]]
    suffix = "" if len(index) <= limit else f" (and {len(index) - limit} more)"
    return f"{shown}{suffix}"
