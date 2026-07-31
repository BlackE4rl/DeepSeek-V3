"""Assembly of the multi-symbol, multi-calendar, multi-currency panel.

The central design decision of this backtester lives here.

Indicators are computed on each symbol's *own* bars. The portfolio then walks a
master index that is the union of every symbol's sessions, and consults an
explicit session mask to know whether a symbol was actually trading on a given
date.

The bars are deliberately **not** forward-filled onto the master index, for
three reasons:

* Filling lets the engine "fill" an order at a stale price on a day the exchange
  was closed. This is the decisive one: it is a pure, silent, optimistic bias,
  and nothing in an equity curve reveals it.
* Filling inserts bars that never happened, so an N-bar moving average spans
  fewer than N actual trading sessions, and by a different amount for each
  exchange. The slope filter then measures something other than what it claims.
* Depending on the fill convention it also corrupts the ATR. Forward-filling the
  whole row duplicates the previous bar's range; forward-filling only the close
  and synthesising ``open == high == low == close`` -- a common shortcut --
  injects zero-range bars and shrinks the ATR outright, which tightens stops and
  inflates position sizes. Both distortions are demonstrated in
  ``tests/test_calendar_alignment.py``.

The remedy is to keep two frames: ``px_open``/``px_high``/``px_low``/
``px_close`` are never filled and are the only prices anything may execute
against, while ``px_mark`` is forward filled and is used exclusively to value
open positions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import Config
from ..strategy import compute_regime, compute_signals
from ..universe import SymbolMeta, Universe
from .fx import FxRates
from .normalize import PriceData


class PanelError(ValueError):
    """Raised when a panel cannot be assembled from the supplied data."""


@dataclass
class Panel:
    """Aligned market data for a backtest run.

    Attributes:
        master_index: Union of every loaded instrument's session dates.
        tradable: Tickers the strategy may open positions in.
        meta: Static description per ticker.
        sessions: Boolean frame, True where a symbol actually had a bar.
        px_open, px_high, px_low, px_close: Execution prices, never filled.
            NaN means the exchange was shut and nothing may trade.
        px_mark: Forward-filled closes, for valuation only.
        signals: Per-symbol rule evaluation, reindexed onto the master index
            without filling.
        regime: Market regime flag, forward-filled from the regime instrument's
            own calendar, so a date without a regime session uses the last known
            state rather than peeking ahead.
        fx: Exchange rates aligned to the master index.
        benchmark_close: Close series of the benchmark instrument, forward filled.
        is_adjusted: Whether each symbol's source supplied a real adjusted close.
        sources: Which CSV dialect each symbol came from.
    """

    master_index: pd.DatetimeIndex
    tradable: Tuple[str, ...]
    meta: Dict[str, SymbolMeta]
    sessions: pd.DataFrame
    px_open: pd.DataFrame
    px_high: pd.DataFrame
    px_low: pd.DataFrame
    px_close: pd.DataFrame
    px_mark: pd.DataFrame
    signals: Dict[str, pd.DataFrame]
    regime: pd.Series
    fx: FxRates
    benchmark_close: pd.Series
    is_adjusted: Dict[str, bool] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    _next_session: Dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for symbol in self.sessions.columns:
            self._next_session[symbol] = _next_session_positions(
                self.sessions[symbol].to_numpy(dtype=bool)
            )

    def has_session(self, symbol: str, position: int) -> bool:
        """Whether ``symbol`` traded on the master index bar at ``position``."""
        return bool(self.sessions.iat[position, self.sessions.columns.get_loc(symbol)])

    def next_session_position(self, symbol: str, position: int) -> int:
        """Position of the symbol's next own session strictly after ``position``.

        This is what makes an order queued at the close of a German holiday eve
        execute on the following Monday rather than on a date the exchange was
        shut.

        Args:
            symbol: Ticker.
            position: Position on the master index.

        Returns:
            The next session's position, or -1 if the symbol has no further
            session in the backtest range.
        """
        return int(self._next_session[symbol][position])

    def currency(self, symbol: str) -> str:
        """Currency a symbol is quoted in."""
        return self.meta[symbol].currency

    def slice(self, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp]) -> "Panel":
        """Restrict the panel to a date range.

        Used by the walk-forward harness, which needs to run the same panel over
        many windows without reloading and recomputing indicators. Indicators are
        *not* recomputed, so a window still benefits from the warm-up that
        happened before its start.

        Args:
            start: Inclusive lower bound, or None.
            end: Inclusive upper bound, or None.

        Returns:
            A new Panel restricted to the range.
        """
        mask = pd.Series(True, index=self.master_index)
        if start is not None:
            mask &= self.master_index >= start
        if end is not None:
            mask &= self.master_index <= end
        index = self.master_index[mask.to_numpy()]
        if len(index) == 0:
            raise PanelError(f"no sessions between {start} and {end}")

        return Panel(
            master_index=index,
            tradable=self.tradable,
            meta=self.meta,
            sessions=self.sessions.loc[index],
            px_open=self.px_open.loc[index],
            px_high=self.px_high.loc[index],
            px_low=self.px_low.loc[index],
            px_close=self.px_close.loc[index],
            px_mark=self.px_mark.loc[index],
            signals={symbol: frame.loc[index] for symbol, frame in self.signals.items()},
            regime=self.regime.loc[index],
            fx=self.fx.align(index),
            benchmark_close=self.benchmark_close.loc[index],
            is_adjusted=self.is_adjusted,
            sources=self.sources,
        )


def build_panel(
    price_data: Dict[str, PriceData],
    universe: Universe,
    config: Config,
    fx: FxRates,
) -> Panel:
    """Assemble a Panel from loaded price data.

    Args:
        price_data: Mapping of ticker to validated PriceData, covering every
            tradable symbol plus the regime instrument and the benchmark.
        universe: The universe definition.
        config: Resolved configuration.
        fx: Exchange rates, not yet aligned.

    Returns:
        The assembled Panel.

    Raises:
        PanelError: If a required instrument is missing.
    """
    regime_symbol = config.strategy.regime_symbol
    benchmark_symbol = config.backtest.benchmark_symbol or universe.benchmark_symbol

    for required, role in ((regime_symbol, "regime"), (benchmark_symbol, "benchmark")):
        if required and required not in price_data:
            raise PanelError(
                f"the {role} instrument {required!r} was not loaded; add it to the "
                "universe's support_symbols"
            )

    master_index = _union_index(price_data)
    if config.backtest.start is not None:
        master_index = master_index[master_index >= pd.Timestamp(config.backtest.start)]
    if config.backtest.end is not None:
        master_index = master_index[master_index <= pd.Timestamp(config.backtest.end)]
    if len(master_index) == 0:
        raise PanelError("no sessions remain after applying the configured date range")

    aligned_fx = fx.align(master_index)
    meta = universe.meta_by_symbol
    symbols = sorted(price_data)

    sessions = pd.DataFrame(False, index=master_index, columns=symbols)
    px_open = pd.DataFrame(np.nan, index=master_index, columns=symbols)
    px_high = pd.DataFrame(np.nan, index=master_index, columns=symbols)
    px_low = pd.DataFrame(np.nan, index=master_index, columns=symbols)
    px_close = pd.DataFrame(np.nan, index=master_index, columns=symbols)

    for symbol in symbols:
        frame = price_data[symbol].frame
        sessions[symbol] = pd.Series(True, index=frame.index).reindex(
            master_index, fill_value=False
        )
        px_open[symbol] = frame["open"].reindex(master_index)
        px_high[symbol] = frame["high"].reindex(master_index)
        px_low[symbol] = frame["low"].reindex(master_index)
        px_close[symbol] = frame["close"].reindex(master_index)

    px_mark = px_close.ffill()

    tradable = tuple(
        symbol for symbol in universe.tradable_symbols if symbol in price_data
    )
    if not tradable:
        raise PanelError("no tradable symbol of the universe was loaded")

    signals: Dict[str, pd.DataFrame] = {}
    for symbol in tradable:
        frame = price_data[symbol].frame
        currency = meta[symbol].currency
        if currency == universe.account_currency:
            fx_native = None
        else:
            fx_native = aligned_fx.series(currency).reindex(frame.index)
        native_signals = compute_signals(frame, config.strategy, fx_native)
        signals[symbol] = _reindex_signals(native_signals, master_index)

    # The regime instrument has its own calendar. On a date it did not trade,
    # the last known state is carried forward: that is the most recent
    # information actually available, and carrying it forward looks backwards
    # only, never ahead.
    regime_native = compute_regime(price_data[regime_symbol].frame, config.strategy)
    regime = (
        regime_native.astype(float).reindex(master_index).ffill().fillna(0.0) > 0.5
    )

    benchmark_close = px_close[benchmark_symbol].ffill() if benchmark_symbol else pd.Series(
        np.nan, index=master_index
    )

    return Panel(
        master_index=master_index,
        tradable=tradable,
        meta=meta,
        sessions=sessions,
        px_open=px_open,
        px_high=px_high,
        px_low=px_low,
        px_close=px_close,
        px_mark=px_mark,
        signals=signals,
        regime=regime,
        fx=aligned_fx,
        benchmark_close=benchmark_close,
        is_adjusted={symbol: data.is_adjusted for symbol, data in price_data.items()},
        sources={symbol: data.source for symbol, data in price_data.items()},
    )


def load_panel(data_dir: str, universe: Universe, config: Config) -> Panel:
    """Load every instrument of a universe from disk and assemble the panel.

    Args:
        data_dir: Directory holding ``ohlcv/`` and ``fx/`` subdirectories.
        universe: The universe definition.
        config: Resolved configuration.

    Returns:
        The assembled Panel.
    """
    from .fx import load_fx_rates
    from .loader import load_universe_prices

    wanted = [meta.symbol for meta in universe.all_symbols]
    for extra in (config.strategy.regime_symbol, config.backtest.benchmark_symbol):
        if extra and extra not in wanted:
            wanted.append(extra)

    price_data = load_universe_prices(
        data_dir,
        wanted,
        price_mode=config.data.price_mode,
        start=None,
        end=None,
    )
    fx = load_fx_rates(data_dir, universe.fx_pairs, universe.account_currency)
    return build_panel(price_data, universe, config, fx)


_BOOLEAN_SIGNAL_COLUMNS = (
    "trend_ok",
    "touched",
    "depth_ok",
    "confirm",
    "liquidity_ok",
    "warm",
    "entry_raw",
    "below_long",
)


def _reindex_signals(
    native_signals: pd.DataFrame, master_index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Move a symbol's signal frame onto the master index without filling.

    Boolean columns are routed through float so that reindexing does not turn
    them into object columns holding NaN. A date the symbol did not trade reads
    as False for every flag: no session, no signal.

    Args:
        native_signals: Signal frame on the symbol's own session index.
        master_index: The master date index.

    Returns:
        The reindexed signal frame.
    """
    numeric = native_signals.copy()
    numeric[list(_BOOLEAN_SIGNAL_COLUMNS)] = numeric[
        list(_BOOLEAN_SIGNAL_COLUMNS)
    ].astype(float)
    reindexed = numeric.reindex(master_index)
    reindexed[list(_BOOLEAN_SIGNAL_COLUMNS)] = (
        reindexed[list(_BOOLEAN_SIGNAL_COLUMNS)].fillna(0.0) > 0.5
    )
    return reindexed


def _union_index(price_data: Dict[str, PriceData]) -> pd.DatetimeIndex:
    """Union of every instrument's session dates, sorted ascending."""
    index = pd.DatetimeIndex([])
    for data in price_data.values():
        index = index.union(data.frame.index)
    return pd.DatetimeIndex(index).sort_values()


def _next_session_positions(has_session: np.ndarray) -> np.ndarray:
    """For each position, the position of the next True strictly after it.

    Args:
        has_session: Boolean array over the master index.

    Returns:
        An integer array of the same length, -1 where no later session exists.
    """
    n = has_session.size
    result = np.full(n, -1, dtype=np.int64)
    last = -1
    for position in range(n - 1, -1, -1):
        result[position] = last
        if has_session[position]:
            last = position
    return result


def describe_panel(panel: Panel) -> List[str]:
    """Human readable summary lines for the report header.

    Args:
        panel: The assembled panel.

    Returns:
        A list of description lines.
    """
    lines = [
        f"master sessions: {len(panel.master_index)}"
        f" from {panel.master_index[0].date()} to {panel.master_index[-1].date()}",
        f"tradable symbols: {len(panel.tradable)}",
    ]
    unadjusted = sorted(
        symbol for symbol, adjusted in panel.is_adjusted.items() if not adjusted
    )
    if unadjusted:
        lines.append(
            "WARNING - no adjusted close available for "
            f"{unadjusted}; splits and dividends are not accounted for in these "
            "series and their signals cannot be trusted"
        )
    return lines
