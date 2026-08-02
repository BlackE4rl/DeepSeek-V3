"""Universe definition: which symbols are traded, in which currency, on which exchange."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from .config import ConfigError


@dataclass(frozen=True)
class SymbolMeta:
    """Static description of one instrument.

    Attributes:
        symbol: Ticker, used as the CSV file stem and as the key everywhere else.
        currency: Currency the instrument's prices are quoted in.
        exchange: Exchange code, used only for reporting and for the synthetic
            calendar generator. The real trading calendar is defined by which
            rows exist in the data, never by this field.
        name: Human readable company name.
        half_spread_bps: Per-symbol override of the cost model's half spread.
            None means fall back to the global default.
        tradable: False for instruments that are loaded but never traded, such
            as the regime instrument and the benchmark.
    """

    symbol: str
    currency: str
    exchange: str = ""
    name: str = ""
    half_spread_bps: Optional[float] = None
    tradable: bool = True


@dataclass(frozen=True)
class FxPair:
    """One currency conversion series.

    Attributes:
        currency: The foreign currency being converted to the account currency.
        file: File stem of the CSV holding the quotes.
        invert: True when the file quotes foreign units per account unit and
            therefore has to be inverted. EURUSD quotes USD per EUR, while the
            engine needs EUR per USD, so invert is true for it.
    """

    currency: str
    file: str
    invert: bool = False


@dataclass(frozen=True)
class Universe:
    """A tradable universe together with its support series.

    Attributes:
        name: Identifier of the universe, used in report headers.
        account_currency: Currency of the cash ledger.
        benchmark_symbol: Primary instrument, also used for the market regime
            filter and for the headline buy-and-hold comparison.
        benchmark_symbols: Every instrument the report compares against. The
            primary one is included automatically. Listing more than one matters
            because they answer different questions: a broad index says whether
            the sector bet paid, while the ETF you would actually have bought
            says whether running the strategy was worth the trouble.
        symbols: The tradable instruments.
        support_symbols: Instruments loaded for signals or benchmarking only.
        fx_pairs: Currency conversion series.
    """

    name: str
    account_currency: str
    benchmark_symbol: str
    symbols: Tuple[SymbolMeta, ...] = field(default_factory=tuple)
    support_symbols: Tuple[SymbolMeta, ...] = field(default_factory=tuple)
    fx_pairs: Tuple[FxPair, ...] = field(default_factory=tuple)
    benchmark_symbols: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_benchmarks(self) -> Tuple[str, ...]:
        """Every benchmark to report against, primary first, without duplicates."""
        ordered: List[str] = []
        for symbol in (self.benchmark_symbol,) + tuple(self.benchmark_symbols):
            if symbol and symbol not in ordered:
                ordered.append(symbol)
        return tuple(ordered)

    @property
    def all_symbols(self) -> Tuple[SymbolMeta, ...]:
        """Every instrument that has to be loaded, tradable or not."""
        return self.symbols + self.support_symbols

    @property
    def tradable_symbols(self) -> Tuple[str, ...]:
        """Tickers of the instruments the strategy may open positions in."""
        return tuple(meta.symbol for meta in self.symbols)

    @property
    def meta_by_symbol(self) -> Dict[str, SymbolMeta]:
        """Lookup from ticker to its static description."""
        return {meta.symbol: meta for meta in self.all_symbols}

    @property
    def currencies(self) -> Tuple[str, ...]:
        """All distinct currencies appearing in the universe."""
        seen: List[str] = []
        for meta in self.all_symbols:
            if meta.currency not in seen:
                seen.append(meta.currency)
        return tuple(seen)

    def require_fx_coverage(self) -> None:
        """Verify that every foreign currency in the universe has an FX pair.

        Raises:
            ConfigError: If a currency other than the account currency has no
                conversion series defined.
        """
        covered = {pair.currency for pair in self.fx_pairs} | {self.account_currency}
        missing = sorted(set(self.currencies) - covered)
        if missing:
            raise ConfigError(
                f"universe {self.name!r} has no FX pair for currencies {missing}; "
                "positions in them could not be valued in the account currency"
            )


def load_universe(path: str) -> Universe:
    """Load a universe definition from a YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed Universe.

    Raises:
        ConfigError: If the file is malformed or a symbol appears twice.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")

    for required in ("name", "account_currency", "symbols"):
        if required not in raw:
            raise ConfigError(f"{path} is missing the required key {required!r}")

    account_currency = str(raw["account_currency"])
    benchmark_symbol = str(raw.get("benchmark_symbol", ""))

    symbols = tuple(_parse_symbol(entry, tradable=True) for entry in raw["symbols"])
    support = tuple(
        _parse_symbol(entry, tradable=False) for entry in (raw.get("support_symbols") or [])
    )
    fx_pairs = tuple(_parse_fx_pair(entry) for entry in (raw.get("fx_pairs") or []))

    tickers = [meta.symbol for meta in symbols + support]
    duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
    if duplicates:
        raise ConfigError(f"{path} lists these symbols more than once: {duplicates}")

    declared_benchmarks = tuple(str(entry) for entry in (raw.get("benchmark_symbols") or []))
    known = {meta.symbol for meta in symbols + support}
    missing = [symbol for symbol in declared_benchmarks if symbol not in known]
    if missing:
        raise ConfigError(
            f"{path} lists benchmark_symbols {missing} that are not declared as "
            "symbols or support_symbols, so they would never be loaded"
        )

    universe = Universe(
        name=str(raw["name"]),
        account_currency=account_currency,
        benchmark_symbol=benchmark_symbol,
        symbols=symbols,
        support_symbols=support,
        fx_pairs=fx_pairs,
        benchmark_symbols=declared_benchmarks,
    )
    universe.require_fx_coverage()
    return universe


def _parse_symbol(entry: Dict[str, object], tradable: bool) -> SymbolMeta:
    """Parse one symbol entry from the universe file.

    Args:
        entry: Mapping with at least `symbol` and `currency`.
        tradable: Whether the instrument may be traded.

    Returns:
        The parsed SymbolMeta.
    """
    if not isinstance(entry, dict):
        raise ConfigError(f"symbol entries must be mappings, got {entry!r}")
    known = {"symbol", "currency", "exchange", "name", "half_spread_bps"}
    unknown = set(entry) - known
    if unknown:
        raise ConfigError(f"unknown keys in symbol entry {entry.get('symbol')!r}: {sorted(unknown)}")
    for required in ("symbol", "currency"):
        if required not in entry:
            raise ConfigError(f"symbol entry {entry!r} is missing {required!r}")

    half_spread = entry.get("half_spread_bps")
    return SymbolMeta(
        symbol=str(entry["symbol"]),
        currency=str(entry["currency"]),
        exchange=str(entry.get("exchange", "")),
        name=str(entry.get("name", "")),
        half_spread_bps=None if half_spread is None else float(half_spread),
        tradable=tradable,
    )


def _parse_fx_pair(entry: Dict[str, object]) -> FxPair:
    """Parse one FX pair entry from the universe file.

    Args:
        entry: Mapping with at least `currency` and `file`.

    Returns:
        The parsed FxPair.
    """
    if not isinstance(entry, dict):
        raise ConfigError(f"fx_pairs entries must be mappings, got {entry!r}")
    known = {"currency", "file", "invert"}
    unknown = set(entry) - known
    if unknown:
        raise ConfigError(f"unknown keys in fx_pairs entry: {sorted(unknown)}")
    for required in ("currency", "file"):
        if required not in entry:
            raise ConfigError(f"fx_pairs entry {entry!r} is missing {required!r}")
    return FxPair(
        currency=str(entry["currency"]),
        file=str(entry["file"]),
        invert=bool(entry.get("invert", False)),
    )
