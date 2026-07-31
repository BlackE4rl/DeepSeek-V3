"""Deterministic synthetic price data.

This module exists for two reasons.

First, the tests must run without network access, and they must produce the same
numbers on every machine. Every generator therefore takes an explicit ``seed``
and uses ``numpy.random.default_rng``; the global RNG is never touched.

Second, this environment's egress policy blocks the market data vendors, so the
whole pipeline is exercised end to end on generated prices. Those runs prove
that the code executes and that the accounting holds. They say nothing at all
about whether the strategy makes money -- for that, real data is required.

The trading calendar is expressed purely by which rows exist: a symbol has a
session on a date if and only if it has a bar there. No market calendar package
is needed, and the multi-exchange handling is exercised by every integration
test because the generated calendars are deliberately not aligned.
"""

import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# Fixed-date holiday approximations per exchange. These are not the real
# calendars and are not meant to be: their purpose is to make the generated
# session indices differ from one another, so that every test that touches more
# than one exchange has to cope with non-aligned dates.
_HOLIDAYS: Dict[str, Tuple[Tuple[int, int], ...]] = {
    "XNYS": ((1, 1), (5, 30), (7, 4), (9, 5), (11, 24), (12, 25)),
    "XNAS": ((1, 1), (5, 30), (7, 4), (9, 5), (11, 24), (12, 25)),
    "XETR": ((1, 1), (5, 1), (10, 3), (12, 24), (12, 25), (12, 26)),
    "XAMS": ((1, 1), (4, 27), (5, 5), (12, 25), (12, 26)),
    "XPAR": ((1, 1), (5, 1), (5, 8), (7, 14), (12, 25), (12, 26)),
    "XSTO": ((1, 1), (1, 6), (5, 1), (6, 6), (12, 24), (12, 25), (12, 26)),
    "XHEL": ((1, 1), (1, 6), (5, 1), (12, 6), (12, 24), (12, 25), (12, 26)),
}


def weekdays(start: str, end: str) -> pd.DatetimeIndex:
    """All Monday-to-Friday dates in a range.

    Args:
        start: First date, ISO format.
        end: Last date, ISO format, inclusive.

    Returns:
        A DatetimeIndex of weekdays.
    """
    return pd.bdate_range(start=start, end=end, freq="C", weekmask="Mon Tue Wed Thu Fri")


def make_sessions(start: str, end: str, exchange: str) -> pd.DatetimeIndex:
    """Generate an exchange's session dates.

    Args:
        start: First date, ISO format.
        end: Last date, ISO format, inclusive.
        exchange: Exchange code. Unknown codes fall back to plain weekdays.

    Returns:
        A DatetimeIndex of session dates, weekdays minus that exchange's
        fixed-date holidays.
    """
    days = weekdays(start, end)
    holidays = _HOLIDAYS.get(exchange)
    if not holidays:
        return days
    holiday_set = set(holidays)
    keep = [(day.month, day.day) not in holiday_set for day in days]
    return days[np.array(keep, dtype=bool)]


def make_gbm_log_returns(
    seed: int, n: int, mu: float, sigma: float, bars_per_year: int = 252
) -> np.ndarray:
    """Geometric Brownian motion log returns.

    Args:
        seed: RNG seed.
        n: Number of returns to generate.
        mu: Annualized drift of the price process.
        sigma: Annualized volatility.
        bars_per_year: Bars per year used to scale the parameters.

    Returns:
        An array of ``n`` log returns.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / bars_per_year
    drift = (mu - 0.5 * sigma * sigma) * dt
    shock = sigma * np.sqrt(dt) * rng.standard_normal(n)
    return drift + shock


def make_gbm_close(
    seed: int, n: int, s0: float = 100.0, mu: float = 0.10, sigma: float = 0.25
) -> np.ndarray:
    """A geometric Brownian motion close series.

    Args:
        seed: RNG seed.
        n: Number of bars.
        s0: Starting price.
        mu: Annualized drift.
        sigma: Annualized volatility.

    Returns:
        An array of ``n`` closes starting at ``s0``.
    """
    log_returns = make_gbm_log_returns(seed, n, mu, sigma)
    log_returns[0] = 0.0
    return s0 * np.exp(np.cumsum(log_returns))


def make_regime_log_returns(
    seed: int, segments: Sequence[Tuple[int, float, float]]
) -> np.ndarray:
    """Log returns stitched from segments with different drift and volatility.

    Used to build a market factor that actually contains bull phases, crashes
    and recoveries, so that the regime filter and the drawdown statistics have
    something to work on.

    Args:
        seed: RNG seed.
        segments: Sequence of ``(n_bars, annual_drift, annual_vol)`` tuples.

    Returns:
        The concatenated log return array.
    """
    parts: List[np.ndarray] = []
    for index, (n_bars, mu, sigma) in enumerate(segments):
        parts.append(make_gbm_log_returns(seed + 1000 * (index + 1), n_bars, mu, sigma))
    return np.concatenate(parts) if parts else np.zeros(0)


def make_regime_close(
    seed: int, segments: Sequence[Tuple[int, float, float]], s0: float = 100.0
) -> np.ndarray:
    """A close series stitched from drift/volatility regimes.

    Args:
        seed: RNG seed.
        segments: Sequence of ``(n_bars, annual_drift, annual_vol)`` tuples.
        s0: Starting price.

    Returns:
        The close price array.
    """
    log_returns = make_regime_log_returns(seed, segments)
    if log_returns.size:
        log_returns[0] = 0.0
    return s0 * np.exp(np.cumsum(log_returns))


def make_scripted_pullback_close(
    n_ramp: int = 320,
    daily_growth: float = 0.0015,
    dip_bars: int = 3,
    dip_per_bar: float = -0.010,
    reclaim: float = 0.030,
    n_tail: int = 40,
    s0: float = 100.0,
) -> Tuple[np.ndarray, int]:
    """A fully deterministic path containing exactly one textbook pullback.

    No RNG is involved. The path is a steady ramp long enough to warm every
    indicator, then a short dip that pushes the lows through the 20-day average
    without breaching the depth limit, then a single strong reclaim bar, then a
    resumed ramp. Because the shape is closed form, the expected signal bar and
    the expected fill price can be worked out by hand, which is what makes the
    strategy rule tests meaningful rather than circular.

    Args:
        n_ramp: Bars in the initial ramp.
        daily_growth: Per-bar growth rate of the ramp.
        dip_bars: Number of declining bars.
        dip_per_bar: Per-bar return during the dip, negative.
        reclaim: Return of the single reclaim bar, positive.
        n_tail: Bars of resumed ramp after the reclaim.
        s0: Starting price.

    Returns:
        A tuple of the close array and the index of the reclaim bar, which is
        the bar the entry signal is expected to fire on.
    """
    steps = [0.0]
    steps.extend([daily_growth] * (n_ramp - 1))
    steps.extend([dip_per_bar] * dip_bars)
    steps.append(reclaim)
    steps.extend([daily_growth] * n_tail)

    closes = s0 * np.cumprod(1.0 + np.asarray(steps, dtype=float))
    reclaim_index = n_ramp + dip_bars
    return closes, reclaim_index


def make_ohlc_from_close(
    closes: np.ndarray,
    index: pd.DatetimeIndex,
    range_frac: float = 0.016,
    gap_frac: float = 0.0,
    base_volume: float = 5_000_000.0,
    volume_noise: float = 0.0,
    seed: Optional[int] = None,
) -> pd.DataFrame:
    """Build OHLCV bars around a close path.

    The bars always satisfy ``high >= max(open, close)`` and
    ``low <= min(open, close)``, which is the invariant the data validator
    enforces on real files as well.

    Args:
        closes: Close price path.
        index: Session dates, same length as ``closes``.
        range_frac: Fraction of price spanned by the bar around its open/close
            envelope; half is added above and half subtracted below.
        gap_frac: Standard deviation of the overnight gap between the previous
            close and the next open. Zero produces open == previous close.
        base_volume: Mean share volume.
        volume_noise: Lognormal volume noise, as a standard deviation.
        seed: RNG seed, required when ``gap_frac`` or ``volume_noise`` is
            non-zero.

    Returns:
        A DataFrame with columns open, high, low, close, adj_close, volume.
    """
    closes = np.asarray(closes, dtype=float)
    if closes.size != len(index):
        raise ValueError(f"closes has {closes.size} values but index has {len(index)} dates")

    rng = np.random.default_rng(seed) if seed is not None else None
    if (gap_frac or volume_noise) and rng is None:
        raise ValueError("a seed is required when gap_frac or volume_noise is non-zero")

    opens = np.empty_like(closes)
    opens[0] = closes[0]
    if gap_frac:
        gaps = rng.normal(0.0, gap_frac, closes.size)
        opens[1:] = closes[:-1] * (1.0 + gaps[1:])
    else:
        opens[1:] = closes[:-1]

    upper = np.maximum(opens, closes)
    lower = np.minimum(opens, closes)
    highs = upper * (1.0 + range_frac / 2.0)
    lows = lower * (1.0 - range_frac / 2.0)

    if volume_noise:
        volumes = base_volume * np.exp(rng.normal(0.0, volume_noise, closes.size))
    else:
        volumes = np.full(closes.size, base_volume, dtype=float)

    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        },
        index=index,
    )


def make_fx_series(
    seed: int,
    index: pd.DatetimeIndex,
    s0: float = 1.10,
    drift: float = 0.0,
    vol: float = 0.08,
) -> pd.Series:
    """A synthetic FX rate series.

    Args:
        seed: RNG seed.
        index: Dates to generate rates for.
        s0: Starting rate.
        drift: Annualized drift.
        vol: Annualized volatility.

    Returns:
        A series of rates indexed by ``index``.
    """
    closes = make_gbm_close(seed, len(index), s0=s0, mu=drift, sigma=vol)
    return pd.Series(closes, index=index, name="close")


def _market_factor(seed: int, index: pd.DatetimeIndex) -> pd.Series:
    """A market-wide log return factor with bull, crash and recovery phases.

    Args:
        seed: RNG seed.
        index: Dates to generate the factor for.

    Returns:
        A log return series indexed by ``index``.
    """
    n = len(index)
    # Roughly: four years up, a crash, a choppy year, five years up, a sharp
    # bear, then a recovery. Proportions are scaled to the requested length.
    shape = [
        (0.28, 0.18, 0.16),
        (0.05, -0.45, 0.40),
        (0.10, 0.02, 0.26),
        (0.30, 0.22, 0.17),
        (0.07, -0.35, 0.38),
        (0.20, 0.16, 0.20),
    ]
    segments: List[Tuple[int, float, float]] = []
    assigned = 0
    for position, (share, mu, sigma) in enumerate(shape):
        bars = n - assigned if position == len(shape) - 1 else int(round(share * n))
        bars = max(bars, 1)
        segments.append((bars, mu, sigma))
        assigned += bars
    returns = make_regime_log_returns(seed, segments)[:n]
    return pd.Series(returns, index=index)


def _symbol_closes(
    seed: int,
    sessions: pd.DatetimeIndex,
    factor: pd.Series,
    beta: float,
    idio_vol: float,
    s0: float,
) -> np.ndarray:
    """Build a symbol's close path from a shared market factor plus own noise.

    The market factor lives on the full weekday grid. A symbol that was closed
    for a holiday accumulates the factor moves that happened while it was shut,
    which is what actually occurs when an exchange reopens.

    Args:
        seed: RNG seed for the idiosyncratic component.
        sessions: The symbol's own session dates.
        factor: Market log returns on the full weekday grid.
        beta: Sensitivity to the market factor.
        idio_vol: Annualized idiosyncratic volatility.
        s0: Starting price.

    Returns:
        The close price array, one value per session.
    """
    # Map every weekday of the factor grid onto the first session that is not
    # before it. A day the exchange was shut therefore lands on the session that
    # reopens, which is where its move actually shows up in the price.
    assignment = np.searchsorted(
        sessions.to_numpy(), factor.index.to_numpy(), side="left"
    )
    grouped = pd.Series(factor.to_numpy(), index=assignment).groupby(level=0).sum()
    market_part = grouped.reindex(range(len(sessions)), fill_value=0.0).to_numpy() * beta

    rng = np.random.default_rng(seed)
    idio = rng.normal(0.0, idio_vol / np.sqrt(252.0), len(sessions))
    log_returns = market_part + idio
    log_returns[0] = 0.0
    return s0 * np.exp(np.cumsum(log_returns))


# Symbol definitions of the synthetic universe: ticker, currency, exchange,
# beta to the market factor, idiosyncratic volatility, starting price.
_SYNTHETIC_SYMBOLS: Tuple[Tuple[str, str, str, float, float, float], ...] = (
    ("SYN-US-A", "USD", "XNAS", 1.20, 0.22, 40.0),
    ("SYN-US-B", "USD", "XNAS", 1.05, 0.18, 85.0),
    ("SYN-US-C", "USD", "XNAS", 1.45, 0.30, 25.0),
    ("SYN-US-D", "USD", "XNAS", 0.90, 0.16, 120.0),
    ("SYN-US-E", "USD", "XNYS", 1.10, 0.20, 60.0),
    ("SYN-US-F", "USD", "XNYS", 1.30, 0.26, 33.0),
    ("SYN-US-G", "USD", "XNAS", 0.80, 0.15, 150.0),
    ("SYN-US-H", "USD", "XNAS", 1.60, 0.35, 18.0),
    ("SYN-US-I", "USD", "XNYS", 1.00, 0.19, 72.0),
    ("SYN-US-J", "USD", "XNAS", 1.15, 0.24, 48.0),
    ("SYN-EU-A", "EUR", "XETR", 0.95, 0.19, 55.0),
    ("SYN-EU-B", "EUR", "XETR", 1.10, 0.23, 38.0),
    ("SYN-EU-C", "EUR", "XAMS", 1.25, 0.27, 90.0),
    ("SYN-EU-D", "EUR", "XAMS", 0.85, 0.17, 110.0),
    ("SYN-EU-E", "EUR", "XPAR", 1.05, 0.21, 44.0),
    ("SYN-EU-F", "EUR", "XHEL", 0.90, 0.25, 15.0),
    ("SYN-SE-A", "SEK", "XSTO", 1.00, 0.28, 95.0),
)

_BENCHMARK_SYMBOL = "SYN-QQQ"


def make_universe(
    out_dir: str,
    seed: int = 42,
    start: str = "2005-01-03",
    end: str = "2026-06-30",
) -> Dict[str, object]:
    """Write a complete synthetic universe to disk in Yahoo CSV format.

    Produces seventeen tradable symbols across five exchanges and three
    currencies, a benchmark that doubles as the regime instrument, and two FX
    series. The exchanges have different holidays on purpose.

    Args:
        out_dir: Directory to write into. ``ohlcv/`` and ``fx/`` subdirectories
            are created.
        seed: Master RNG seed.
        start: First date.
        end: Last date.

    Returns:
        A manifest dictionary describing what was written.
    """
    ohlcv_dir = os.path.join(out_dir, "ohlcv")
    fx_dir = os.path.join(out_dir, "fx")
    os.makedirs(ohlcv_dir, exist_ok=True)
    os.makedirs(fx_dir, exist_ok=True)

    grid = weekdays(start, end)
    factor = _market_factor(seed, grid)

    written: List[str] = []

    benchmark_sessions = make_sessions(start, end, "XNAS")
    benchmark_closes = _symbol_closes(
        seed + 1, benchmark_sessions, factor, beta=1.0, idio_vol=0.04, s0=50.0
    )
    benchmark_frame = make_ohlc_from_close(
        benchmark_closes,
        benchmark_sessions,
        range_frac=0.014,
        gap_frac=0.004,
        base_volume=50_000_000.0,
        volume_noise=0.30,
        seed=seed + 1,
    )
    _write_yahoo_csv(benchmark_frame, os.path.join(ohlcv_dir, f"{_BENCHMARK_SYMBOL}.csv"))
    written.append(_BENCHMARK_SYMBOL)

    for offset, (symbol, currency, exchange, beta, idio, s0) in enumerate(_SYNTHETIC_SYMBOLS):
        sessions = make_sessions(start, end, exchange)
        closes = _symbol_closes(seed + 100 + offset, sessions, factor, beta, idio, s0)
        frame = make_ohlc_from_close(
            closes,
            sessions,
            range_frac=0.020,
            gap_frac=0.006,
            base_volume=8_000_000.0,
            volume_noise=0.40,
            seed=seed + 200 + offset,
        )
        _write_yahoo_csv(frame, os.path.join(ohlcv_dir, f"{symbol}.csv"))
        written.append(symbol)

    fx_files = {}
    for offset, (pair, s0, vol) in enumerate((("EURUSD=X", 1.15, 0.09), ("EURSEK=X", 10.5, 0.07))):
        rates = make_fx_series(seed + 500 + offset, grid, s0=s0, drift=0.0, vol=vol)
        fx_frame = pd.DataFrame(
            {
                "open": rates,
                "high": rates * 1.002,
                "low": rates * 0.998,
                "close": rates,
                "adj_close": rates,
                "volume": 0.0,
            },
            index=grid,
        )
        _write_yahoo_csv(fx_frame, os.path.join(fx_dir, f"{pair}.csv"))
        fx_files[pair] = os.path.join("fx", f"{pair}.csv")

    return {
        "source": "synthetic",
        "seed": seed,
        "start": start,
        "end": end,
        "symbols": written,
        "fx": sorted(fx_files),
        "benchmark": _BENCHMARK_SYMBOL,
    }


def synthetic_universe_yaml(account_currency: str = "EUR") -> str:
    """Return a universe YAML document describing the synthetic universe.

    Args:
        account_currency: Currency of the cash ledger.

    Returns:
        The YAML text, ready to be written next to the generated CSV files.
    """
    lines = [
        "# Generated by trendfolge.datasets.synthetic. Prices are simulated.",
        "# Any performance number produced from this universe is meaningless as",
        "# evidence about the strategy; it only proves the code runs.",
        "name: synthetic",
        f"account_currency: {account_currency}",
        f"benchmark_symbol: {_BENCHMARK_SYMBOL}",
        "",
        "support_symbols:",
        f"  - {{symbol: {_BENCHMARK_SYMBOL}, currency: USD, exchange: XNAS, name: Synthetic benchmark}}",
        "",
        "fx_pairs:",
        '  - {currency: USD, file: "EURUSD=X", invert: true}',
        '  - {currency: SEK, file: "EURSEK=X", invert: true}',
        "",
        "symbols:",
    ]
    for symbol, currency, exchange, _beta, _idio, _s0 in _SYNTHETIC_SYMBOLS:
        lines.append(
            f"  - {{symbol: {symbol}, currency: {currency}, exchange: {exchange}, "
            f"name: {symbol}, half_spread_bps: 3.0}}"
        )
    return "\n".join(lines) + "\n"


def _write_yahoo_csv(frame: pd.DataFrame, path: str) -> None:
    """Write a canonical frame in Yahoo Finance CSV format.

    Args:
        frame: Canonical OHLCV frame.
        path: Destination path.
    """
    out = pd.DataFrame(
        {
            "Date": frame.index.strftime("%Y-%m-%d"),
            "Open": frame["open"].round(6),
            "High": frame["high"].round(6),
            "Low": frame["low"].round(6),
            "Close": frame["close"].round(6),
            "Adj Close": frame["adj_close"].round(6),
            "Volume": frame["volume"].round(0).astype("int64"),
        }
    )
    out.to_csv(path, index=False)
