"""Fetch real daily bars from Yahoo Finance into the local CSV cache.

This is the only module in the project that touches the network, and nothing in
the analytical core imports it. Run it on your own machine:

    pip install -r requirements-download.txt
    python -m trendfolge.cli.download_data \\
        --universe configs/universe_megacap.yaml \\
        --start 2005-01-01 --out data

It writes one CSV per symbol in Yahoo format, the FX series the universe needs,
and a manifest recording when the data was fetched and what it contained, so a
report can always name the data it was computed from.
"""

import argparse
import datetime as _datetime
import hashlib
import os
import time
from typing import Dict, List, Optional

from ..universe import load_universe
from ._common import write_manifest

_INSTALL_HINT = (
    "yfinance is not installed. It is deliberately kept out of the core "
    "requirements so the backtester never depends on a network library.\n"
    "Install it with:  pip install -r requirements-download.txt"
)


def _import_yfinance():
    """Import yfinance, with an actionable message when it is missing."""
    try:
        import yfinance  # noqa: WPS433 (deliberate local import)
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise SystemExit(_INSTALL_HINT) from error
    return yfinance


def download_symbol(
    yfinance,
    symbol: str,
    start: str,
    end: Optional[str],
    destination: str,
    retries: int = 4,
    pause: float = 1.0,
) -> int:
    """Download one symbol's daily bars and write them as a Yahoo-format CSV.

    Args:
        yfinance: The imported yfinance module.
        symbol: Ticker to fetch.
        start: First date, ISO format.
        end: Last date, ISO format, or None for today.
        destination: Path of the CSV file to write.
        retries: How many times to retry a failed or empty download.
        pause: Seconds to wait between attempts, doubled on each retry.

    Returns:
        The number of rows written.

    Raises:
        RuntimeError: If the download never returned any data.
    """
    delay = pause
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            frame = yfinance.download(
                symbol,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if frame is not None and not frame.empty:
                _write(frame, symbol, destination)
                return len(frame)
        except Exception as error:  # pragma: no cover - network behaviour
            last_error = error
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(
        f"no data returned for {symbol} after {retries} attempts"
        + (f": {last_error}" if last_error else "")
    )


def _write(frame, symbol: str, destination: str) -> None:
    """Normalize a yfinance frame to the Yahoo CSV layout and write it."""
    import pandas as pd

    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.droplevel(1, axis=1)

    frame = frame.rename(columns={"Adj Close": "Adj Close"})
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column not in frame.columns:
            raise RuntimeError(f"{symbol}: download is missing the {column} column")
    if "Adj Close" not in frame.columns:
        frame["Adj Close"] = frame["Close"]

    out = frame[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].copy()
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out.index.name = "Date"
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    out.to_csv(destination, date_format="%Y-%m-%d")


def _sha256(path: str) -> str:
    """Hash a file so a report can prove which bytes it was computed from."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True, help="universe YAML file")
    parser.add_argument("--start", default="2005-01-01", help="first date")
    parser.add_argument("--end", default=None, help="last date, defaults to today")
    parser.add_argument("--out", required=True, help="data directory to fill")
    parser.add_argument(
        "--throttle", type=float, default=0.4, help="seconds to wait between symbols"
    )
    args = parser.parse_args()

    yfinance = _import_yfinance()
    universe = load_universe(args.universe)

    ohlcv_dir = os.path.join(args.out, "ohlcv")
    fx_dir = os.path.join(args.out, "fx")
    os.makedirs(ohlcv_dir, exist_ok=True)
    os.makedirs(fx_dir, exist_ok=True)

    files: Dict[str, Dict[str, object]] = {}
    failures: List[str] = []

    for meta in universe.all_symbols:
        destination = os.path.join(ohlcv_dir, f"{meta.symbol}.csv")
        try:
            rows = download_symbol(yfinance, meta.symbol, args.start, args.end, destination)
        except RuntimeError as error:
            print(f"FAILED {meta.symbol}: {error}")
            failures.append(meta.symbol)
            continue
        files[meta.symbol] = {"rows": rows, "sha256": _sha256(destination)}
        print(f"{meta.symbol}: {rows} rows")
        time.sleep(args.throttle)

    for pair in universe.fx_pairs:
        destination = os.path.join(fx_dir, f"{pair.file}.csv")
        try:
            rows = download_symbol(yfinance, pair.file, args.start, args.end, destination)
        except RuntimeError as error:
            print(f"FAILED {pair.file}: {error}")
            failures.append(pair.file)
            continue
        files[pair.file] = {"rows": rows, "sha256": _sha256(destination)}
        print(f"{pair.file}: {rows} rows")
        time.sleep(args.throttle)

    # The universe file is copied next to the data so a run can be reproduced
    # from the data directory alone.
    with open(args.universe, "r", encoding="utf-8") as source:
        text = source.read()
    with open(os.path.join(args.out, "universe.yaml"), "w", encoding="utf-8") as handle:
        handle.write(text)

    write_manifest(
        args.out,
        {
            "source": "yfinance",
            "downloaded_at": _datetime.datetime.now().isoformat(timespec="seconds"),
            "universe": universe.name,
            "start": args.start,
            "end": args.end or "today",
            "symbols": sorted(files),
            "files": files,
            "failures": failures,
        },
    )

    if failures:
        print(
            f"\n{len(failures)} symbol(s) could not be downloaded: {failures}\n"
            "Fix or remove them in the universe file before trusting a backtest: a "
            "silently missing symbol changes which trades the strategy could take."
        )
    print(f"\nmanifest written to {os.path.join(args.out, '_manifest.json')}")


if __name__ == "__main__":
    main()
