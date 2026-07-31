"""CSV loading for daily bars.

Three vendor dialects are recognised by sniffing the header, so the same cache
directory can hold files exported from Yahoo Finance, Stooq and TradingView
without the caller having to say which is which.

Nothing in this module touches the network. Fetching real data is the job of
``trendfolge/cli/download_data.py``, which is the only file in the project that
imports a networking library.
"""

import os
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .normalize import (
    DataValidationError,
    PriceData,
    apply_price_mode,
    slice_dates,
    to_canonical,
)

# Vendor header -> canonical column name. Keys are lowercased and stripped.
_COLUMN_ALIASES = {
    "date": "date",
    "time": "date",
    "datetime": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "close/last": "close",
    "adj close": "adj_close",
    "adj_close": "adj_close",
    "adjclose": "adj_close",
    "volume": "volume",
    "vol.": "volume",
}

_REQUIRED = ("date", "open", "high", "low", "close")


def detect_dialect(columns: List[str]) -> str:
    """Identify the vendor a CSV came from by its header.

    Args:
        columns: The raw header row.

    Returns:
        One of ``yahoo``, ``stooq`` or ``tradingview``.

    Raises:
        DataValidationError: If the header lacks the required columns.
    """
    lowered = [str(column).strip().lower() for column in columns]
    mapped = {_COLUMN_ALIASES.get(column) for column in lowered}

    missing = [name for name in _REQUIRED if name not in mapped]
    if missing:
        raise DataValidationError(
            f"CSV header {columns} is missing required columns {missing}"
        )

    if "adj_close" in mapped:
        return "yahoo"
    if lowered[0] in ("time", "datetime"):
        return "tradingview"
    return "stooq"


def read_price_csv(path: str) -> Tuple[pd.DataFrame, str, bool]:
    """Read a daily bar CSV in any of the supported dialects.

    Args:
        path: Path to the CSV file.

    Returns:
        A tuple of the raw frame indexed by date, the detected dialect, and
        whether a genuine adjusted close was present.

    Raises:
        DataValidationError: If the header cannot be interpreted.
    """
    raw = pd.read_csv(path)
    if raw.empty:
        raise DataValidationError(f"{path} contains no rows")

    dialect = detect_dialect(list(raw.columns))

    renamed = {}
    for column in raw.columns:
        canonical = _COLUMN_ALIASES.get(str(column).strip().lower())
        if canonical is not None:
            renamed[column] = canonical
    frame = raw.rename(columns=renamed)
    frame = frame.loc[:, ~frame.columns.duplicated()]

    keep = [name for name in ("date", "open", "high", "low", "close", "adj_close", "volume") if name in frame.columns]
    frame = frame[keep]

    frame = frame.set_index(_parse_dates(frame["date"], path)).drop(columns=["date"])
    has_adjusted = "adj_close" in frame.columns
    return frame, dialect, has_adjusted


def load_price_data(
    path: str,
    symbol: str,
    price_mode: str = "adjusted",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> PriceData:
    """Load, validate and adjust one symbol's daily bars.

    Args:
        path: Path to the CSV file.
        symbol: Ticker the file belongs to.
        price_mode: ``adjusted`` or ``raw``.
        start: Inclusive lower date bound, or None.
        end: Inclusive upper date bound, or None.

    Returns:
        The validated PriceData.
    """
    frame, dialect, has_adjusted = read_price_csv(path)
    canonical = to_canonical(frame, symbol, has_adjusted)
    priced = apply_price_mode(canonical, price_mode, symbol)
    sliced = slice_dates(priced, start, end)
    if sliced.empty:
        raise DataValidationError(
            f"{symbol}: no bars left after restricting to {start}..{end}"
        )
    return PriceData(
        symbol=symbol,
        frame=sliced,
        is_adjusted=has_adjusted,
        source=dialect,
        path=path,
    )


def find_price_file(directory: str, symbol: str) -> str:
    """Locate the CSV file holding a symbol's bars.

    Args:
        directory: Directory to search.
        symbol: Ticker.

    Returns:
        The path to the file.

    Raises:
        FileNotFoundError: If no matching file exists.
    """
    for candidate in (f"{symbol}.csv", f"{symbol.upper()}.csv", f"{symbol.replace('.', '_')}.csv"):
        path = os.path.join(directory, candidate)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        f"no price file for {symbol!r} in {directory}; expected {symbol}.csv"
    )


def load_universe_prices(
    data_dir: str,
    symbols: List[str],
    price_mode: str = "adjusted",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, PriceData]:
    """Load every symbol of a universe from a data directory.

    Args:
        data_dir: Directory containing an ``ohlcv`` subdirectory.
        symbols: Tickers to load.
        price_mode: ``adjusted`` or ``raw``.
        start: Inclusive lower date bound, or None.
        end: Inclusive upper date bound, or None.

    Returns:
        Mapping of ticker to PriceData.
    """
    ohlcv_dir = os.path.join(data_dir, "ohlcv")
    if not os.path.isdir(ohlcv_dir):
        ohlcv_dir = data_dir

    loaded: Dict[str, PriceData] = {}
    for symbol in symbols:
        path = find_price_file(ohlcv_dir, symbol)
        loaded[symbol] = load_price_data(path, symbol, price_mode, start, end)
    return loaded


def _parse_dates(column: pd.Series, path: str) -> pd.Index:
    """Parse a date column that may hold ISO strings or unix timestamps.

    TradingView exports the bar time as seconds since the epoch, while the other
    vendors write ISO dates.

    Args:
        column: The raw date column.
        path: File path, used in error messages.

    Returns:
        The parsed index.
    """
    if pd.api.types.is_numeric_dtype(column):
        magnitude = column.abs().max()
        unit = "ms" if magnitude > 1e11 else "s"
        return pd.to_datetime(column, unit=unit, errors="coerce")

    parsed = pd.to_datetime(column, errors="coerce", format="mixed")
    if parsed.isna().all():
        raise DataValidationError(f"{path}: could not parse any date in the first column")
    return parsed
