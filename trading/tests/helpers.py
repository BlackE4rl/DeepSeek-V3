"""Shared builders for the test suite.

Everything here is deterministic and offline. Tests that need a full pipeline
generate a small synthetic universe on disk; tests that need to reason about
exact fills build a tiny hand-made panel instead, so the expected numbers can be
written out by hand.
"""

import os
from typing import Dict, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from trendfolge.config import Config
from trendfolge.datasets import synthetic
from trendfolge.datasets.fx import FxRates
from trendfolge.datasets.normalize import PriceData
from trendfolge.datasets.panel import Panel, build_panel
from trendfolge.universe import FxPair, SymbolMeta, Universe


def write_yahoo_csv(path: str, frame: pd.DataFrame) -> None:
    """Write a canonical frame as a Yahoo-format CSV."""
    synthetic._write_yahoo_csv(frame, path)


def make_price_data(
    symbol: str,
    frame: pd.DataFrame,
    is_adjusted: bool = True,
    source: str = "test",
) -> PriceData:
    """Wrap a frame as PriceData without going through a file."""
    return PriceData(symbol=symbol, frame=frame, is_adjusted=is_adjusted, source=source)


def ramp_frame(
    index: pd.DatetimeIndex,
    start_price: float = 100.0,
    daily_growth: float = 0.0015,
    range_frac: float = 0.020,
    base_volume: float = 8_000_000.0,
) -> pd.DataFrame:
    """A smooth rising bar series with no pullbacks, used as filler."""
    steps = np.full(len(index), daily_growth)
    steps[0] = 0.0
    closes = start_price * np.cumprod(1.0 + steps)
    return synthetic.make_ohlc_from_close(
        closes, index, range_frac=range_frac, base_volume=base_volume
    )


def pullback_frame(
    index: pd.DatetimeIndex,
    start_price: float = 100.0,
    base_volume: float = 8_000_000.0,
    **kwargs: float,
) -> "tuple[pd.DataFrame, int]":
    """A bar series containing exactly one textbook pullback setup.

    Args:
        index: Session dates. Must be at least as long as the generated path.
        start_price: Starting price.
        base_volume: Constant share volume.
        **kwargs: Forwarded to ``make_scripted_pullback_close``.

    Returns:
        The frame and the integer position of the expected signal bar.
    """
    closes, reclaim = synthetic.make_scripted_pullback_close(s0=start_price, **kwargs)
    if len(index) < len(closes):
        raise ValueError(f"index has {len(index)} dates, path needs {len(closes)}")
    frame = synthetic.make_ohlc_from_close(
        closes, index[: len(closes)], range_frac=0.020, base_volume=base_volume
    )
    return frame, reclaim


def flat_frame(
    index: pd.DatetimeIndex, price: float = 100.0, base_volume: float = 8_000_000.0
) -> pd.DataFrame:
    """A perfectly flat bar series, used for the regime and benchmark stubs."""
    closes = np.full(len(index), price)
    return synthetic.make_ohlc_from_close(
        closes, index, range_frac=0.004, base_volume=base_volume
    )


def rising_regime_frame(index: pd.DatetimeIndex, price: float = 100.0) -> pd.DataFrame:
    """A regime instrument that is always above its own long average."""
    return ramp_frame(index, start_price=price, daily_growth=0.0010, range_frac=0.006)


def make_universe(
    symbols: Sequence[SymbolMeta],
    account_currency: str = "EUR",
    regime_symbol: str = "REGIME",
    fx_pairs: Iterable[FxPair] = (),
) -> Universe:
    """Assemble a Universe for tests."""
    return Universe(
        name="test",
        account_currency=account_currency,
        benchmark_symbol=regime_symbol,
        symbols=tuple(symbols),
        support_symbols=(
            SymbolMeta(
                symbol=regime_symbol,
                currency=account_currency,
                exchange="XNAS",
                name="regime",
                tradable=False,
            ),
        ),
        fx_pairs=tuple(fx_pairs),
    )


def make_panel(
    frames: Dict[str, pd.DataFrame],
    universe: Universe,
    config: Config,
    fx_rates: Optional[Dict[str, pd.Series]] = None,
) -> Panel:
    """Build a Panel directly from in-memory frames."""
    price_data = {symbol: make_price_data(symbol, frame) for symbol, frame in frames.items()}
    fx = FxRates(universe.account_currency, fx_rates or {})
    return build_panel(price_data, universe, config, fx)


def default_test_config(**overrides: Dict[str, object]) -> Config:
    """A Config with the regime instrument renamed to the test stub."""
    config = Config().with_overrides(
        strategy={"regime_symbol": "REGIME"},
        backtest={"benchmark_symbol": "REGIME"},
    )
    if overrides:
        config = config.with_overrides(**overrides)
    return config


def synthetic_data_dir(tmp_path, seed: int = 7, start: str = "2010-01-01",
                       end: str = "2020-12-31") -> str:
    """Generate a synthetic universe on disk and return its directory."""
    directory = os.path.join(str(tmp_path), "data")
    os.makedirs(directory, exist_ok=True)
    synthetic.make_universe(directory, seed=seed, start=start, end=end)
    universe_path = os.path.join(directory, "universe.yaml")
    with open(universe_path, "w", encoding="utf-8") as handle:
        handle.write(synthetic.synthetic_universe_yaml())
    return directory
