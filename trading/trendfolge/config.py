"""Configuration objects for the backtest.

Every tunable value lives in one of the frozen dataclasses below and is loaded
from a YAML file. The resolved configuration is hashed (SHA-256 over its
canonical JSON form) and the hash is embedded in every report, so a result can
always be traced back to the exact parameter set that produced it.
"""

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Dict, Optional

import yaml


class ConfigError(ValueError):
    """Raised when a configuration file is malformed or internally inconsistent."""


@dataclass(frozen=True)
class StrategyParams:
    """Parameters of the signal logic.

    Attributes:
        sma_long: Window of the long-term trend average.
        sma_slope_lookback: The long average must exceed its value this many bars ago.
        sma_mid: Window of the intermediate average used for the stacking filter.
        sma_pullback: Window of the average price pulls back to. This is the
            reference the entry is built around.
        pullback_touch_window: A low must have touched the pullback average
            within this many bars (inclusive of the signal bar).
        pullback_max_depth_atr: The deepest low in that window may not be more
            than this many ATRs below the pullback average.
        atr_period: Period of the Wilder ATR.
        atr_stop_mult: Initial stop distance, in ATRs below the fill.
        atr_trail_mult: Chandelier trailing distance, in ATRs below the running
            maximum close since entry.
        regime_symbol: Instrument whose trend gates new entries.
        regime_sma: Window of the regime average.
        exit_on_regime_off: If true, open positions are closed when the regime
            turns off. If false (default) the regime only blocks new entries.
        min_price_local: Minimum price in the symbol's own currency.
        min_adv_acct: Minimum 20-day average traded value in account currency.
        cooldown_bars: Bars that must pass after an exit before the same symbol
            may be entered again.
        time_stop_bars: Force an exit after this many bars. 0 disables it.
    """

    sma_long: int = 200
    sma_slope_lookback: int = 20
    sma_mid: int = 50
    sma_pullback: int = 20
    pullback_touch_window: int = 5
    pullback_max_depth_atr: float = 1.5
    atr_period: int = 14
    atr_stop_mult: float = 2.5
    atr_trail_mult: float = 3.5
    regime_symbol: str = "QQQ"
    regime_sma: int = 200
    exit_on_regime_off: bool = False
    min_price_local: float = 5.0
    min_adv_acct: float = 20_000_000.0
    cooldown_bars: int = 5
    time_stop_bars: int = 0

    def __post_init__(self) -> None:
        if self.sma_pullback >= self.sma_mid or self.sma_mid >= self.sma_long:
            raise ConfigError(
                "moving average windows must satisfy "
                f"sma_pullback < sma_mid < sma_long, got {self.sma_pullback}, "
                f"{self.sma_mid}, {self.sma_long}"
            )
        if self.atr_trail_mult <= 0 or self.atr_stop_mult <= 0:
            raise ConfigError("ATR multiples must be positive")
        if self.pullback_touch_window < 1:
            raise ConfigError("pullback_touch_window must be at least 1")
        if self.atr_period < 2:
            raise ConfigError("atr_period must be at least 2")

    @property
    def warmup_bars(self) -> int:
        """Number of bars required before any signal may be produced.

        Returns:
            The largest lookback used by any indicator, so that no signal is
            ever produced from a half-warm moving average.
        """
        return max(
            self.sma_long + self.sma_slope_lookback,
            self.sma_mid,
            self.sma_pullback + self.pullback_touch_window,
            self.atr_period + 1,
        )


@dataclass(frozen=True)
class PortfolioParams:
    """Capital, sizing and exposure limits.

    Attributes:
        initial_equity: Starting equity in the account currency.
        account_currency: Currency the cash ledger and all reporting use.
        risk_per_trade: Fraction of equity risked between entry and initial stop.
        max_positions: Maximum number of simultaneously open positions.
        max_position_weight: Notional cap per position as a fraction of equity.
        max_portfolio_heat: Cap on the sum of open risk across all positions.
        max_adv_participation: Cap on the order size as a fraction of the
            symbol's 20-day average volume.
        allow_fractional_shares: Whether share counts may be fractional.
        max_order_age_days: Cancel a pending order that could not be filled
            within this many calendar days.
    """

    initial_equity: float = 100_000.0
    account_currency: str = "EUR"
    risk_per_trade: float = 0.0075
    max_positions: int = 6
    max_position_weight: float = 0.25
    max_portfolio_heat: float = 0.06
    max_adv_participation: float = 0.05
    allow_fractional_shares: bool = False
    max_order_age_days: int = 5

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ConfigError("initial_equity must be positive")
        if not 0 < self.risk_per_trade <= 1:
            raise ConfigError("risk_per_trade must be in (0, 1]")
        if self.max_positions < 1:
            raise ConfigError("max_positions must be at least 1")
        if not 0 < self.max_position_weight <= 1:
            raise ConfigError("max_position_weight must be in (0, 1]")


@dataclass(frozen=True)
class CostParams:
    """Transaction cost model, charged on both sides of every trade.

    Attributes:
        commission_fixed_acct: Flat fee per side in account currency.
        commission_bps: Variable fee in basis points of traded notional.
        min_commission_acct: Floor applied to the per-side commission.
        slippage_bps: Adverse price move on a market-on-open order.
        half_spread_bps: Half the quoted spread; the universe file may override
            this per symbol.
        stop_extra_slippage_bps: Additional slippage on stop executions, which
            fill into a moving market.
    """

    commission_fixed_acct: float = 1.0
    commission_bps: float = 0.0
    min_commission_acct: float = 1.0
    slippage_bps: float = 5.0
    half_spread_bps: float = 3.0
    stop_extra_slippage_bps: float = 10.0

    def __post_init__(self) -> None:
        negative = [f.name for f in fields(self) if getattr(self, f.name) < 0]
        if negative:
            raise ConfigError(f"cost parameters must not be negative: {negative}")

    def scaled(self, factor: float) -> "CostParams":
        """Return a copy with every cost component multiplied by `factor`.

        Used by the cost stress test. A strategy that dies at twice the modelled
        costs was never real to begin with.

        Args:
            factor: Multiplier applied to all components.

        Returns:
            A new CostParams instance.
        """
        return CostParams(
            commission_fixed_acct=self.commission_fixed_acct * factor,
            commission_bps=self.commission_bps * factor,
            min_commission_acct=self.min_commission_acct * factor,
            slippage_bps=self.slippage_bps * factor,
            half_spread_bps=self.half_spread_bps * factor,
            stop_extra_slippage_bps=self.stop_extra_slippage_bps * factor,
        )

    @staticmethod
    def zero() -> "CostParams":
        """Return a cost-free model, used only for the gross/net comparison."""
        return CostParams(
            commission_fixed_acct=0.0,
            commission_bps=0.0,
            min_commission_acct=0.0,
            slippage_bps=0.0,
            half_spread_bps=0.0,
            stop_extra_slippage_bps=0.0,
        )


@dataclass(frozen=True)
class DataParams:
    """Data handling options.

    Attributes:
        price_mode: "adjusted" back-adjusts the whole bar by adj_close/close so
            splits and dividends do not create phantom signals. "raw" leaves the
            bars untouched and exists only as a cross-check.
    """

    price_mode: str = "adjusted"

    def __post_init__(self) -> None:
        if self.price_mode not in ("adjusted", "raw"):
            raise ConfigError(f"price_mode must be 'adjusted' or 'raw', got {self.price_mode!r}")


@dataclass(frozen=True)
class BacktestParams:
    """Run-level options.

    Attributes:
        start: First date to trade, ISO format, or None for the earliest available.
        end: Last date to trade, ISO format, or None for the latest available.
        benchmark_symbol: Buy-and-hold instrument the strategy is measured against.
        risk_free_rate: Annualized risk-free rate used by Sharpe and Sortino.
        debug_assertions: Whether to verify the cash and equity invariants on
            every bar. Costs a little speed and catches accounting bugs early.
    """

    start: Optional[str] = None
    end: Optional[str] = None
    benchmark_symbol: str = "QQQ"
    risk_free_rate: float = 0.0
    debug_assertions: bool = True


# Basiszins per § 18 Abs. 4 InvStG, announced by the BMF each January and used
# to compute the Vorabpauschale of an accumulating fund. 2021 and 2022 were
# negative, which means no Vorabpauschale was levied at all; they are recorded
# as zero. These are inputs, not constants of nature -- every report prints the
# table it used so the figures can be checked against the BMF-Schreiben.
DEFAULT_BASISZINS: Dict[int, float] = {
    2018: 0.0087,
    2019: 0.0052,
    2020: 0.0007,
    2021: 0.0,
    2022: 0.0,
    2023: 0.0255,
    2024: 0.0229,
    2025: 0.0253,
    2026: 0.0320,
}


@dataclass(frozen=True)
class TaxParams:
    """German capital income tax, as it applies to a private investor.

    Two regimes are modelled because they genuinely differ, and the difference
    is large enough to decide whether an active strategy is worth running at
    all:

    * **Directly held shares** -- what this strategy trades. Every realized gain
      is taxed immediately at the full rate. There is no Teilfreistellung:
      that relief applies to fund units, not to shares held in your own name.
    * **An accumulating equity fund** -- what an ETF like A142N1 is. Thirty
      percent of the gain is exempt, and the tax on the price gain is deferred
      until the units are sold; in the meantime only the small Vorabpauschale
      is due.

    Attributes:
        capital_gains_rate: Abgeltungsteuer, 25 % since 2009.
        solidarity_surcharge: Solidaritätszuschlag on the tax itself, 5.5 %.
        church_tax: Kirchensteuer rate, 0.08 or 0.09 where it applies, else 0.
        annual_allowance: Sparerpauschbetrag, 1,000 EUR for a single filer.
        fund_partial_exemption: Teilfreistellung for equity funds, 30 %.
        basiszins: Base interest rate per calendar year.
        settlement: How the tax is collected. ``withholding`` is a German
            broker, which deducts at every realizing trade. ``assessment`` is a
            foreign broker such as DEGIRO, which withholds nothing: the gains go
            into the annual tax return and the bill arrives with the assessment,
            months later. The difference is real money, because the untaxed gain
            keeps compounding in the meantime.
        payment_lag_months: How long after the end of a tax year the bill is
            actually paid under ``assessment``.
        enabled: When false, every tax function is the identity, so a run can be
            compared before and after tax.
    """

    capital_gains_rate: float = 0.25
    solidarity_surcharge: float = 0.055
    church_tax: float = 0.0
    annual_allowance: float = 1000.0
    fund_partial_exemption: float = 0.30
    basiszins: Dict[int, float] = field(default_factory=lambda: dict(DEFAULT_BASISZINS))
    settlement: str = "assessment"
    payment_lag_months: int = 12
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.settlement not in ("assessment", "withholding"):
            raise ConfigError(
                "settlement must be 'assessment' (foreign broker, taxed via the "
                f"annual return) or 'withholding' (German broker), got {self.settlement!r}"
            )
        if self.payment_lag_months < 0:
            raise ConfigError("payment_lag_months must not be negative")
        if not 0 <= self.capital_gains_rate < 1:
            raise ConfigError("capital_gains_rate must be in [0, 1)")
        if not 0 <= self.fund_partial_exemption < 1:
            raise ConfigError("fund_partial_exemption must be in [0, 1)")
        if self.annual_allowance < 0:
            raise ConfigError("annual_allowance must not be negative")
        normalized = {int(year): float(rate) for year, rate in dict(self.basiszins).items()}
        object.__setattr__(self, "basiszins", normalized)

    @property
    def effective_rate(self) -> float:
        """Total tax on one euro of taxable capital income.

        Without church tax this is simply the 25 % Abgeltungsteuer plus 5.5 %
        solidarity surcharge on it, so 26.375 %. Church tax is not merely added:
        it reduces the Abgeltungsteuer base, which is why the divisor
        ``4 + church_tax`` appears -- that is the formula in § 32d Abs. 1 EStG.

        Returns:
            The combined rate as a fraction.
        """
        if self.church_tax:
            base = 1.0 / (4.0 + self.church_tax)
        else:
            base = self.capital_gains_rate
        return base * (1.0 + self.solidarity_surcharge + self.church_tax)

    def basiszins_for(self, year: int) -> float:
        """Base interest rate for one year.

        Args:
            year: Calendar year.

        Returns:
            The rate, or 0.0 for a year the table does not cover. Returning zero
            rather than raising means an unmapped year simply levies no
            Vorabpauschale, which is the conservative direction for the fund's
            competitor -- and the report names the covered range.
        """
        return float(self.basiszins.get(int(year), 0.0))


@dataclass(frozen=True)
class Config:
    """The complete resolved configuration of a run."""

    strategy: StrategyParams = StrategyParams()
    portfolio: PortfolioParams = PortfolioParams()
    costs: CostParams = CostParams()
    data: DataParams = DataParams()
    backtest: BacktestParams = BacktestParams()
    tax: TaxParams = field(default_factory=TaxParams)

    def to_dict(self) -> Dict[str, Any]:
        """Return the configuration as a plain nested dictionary."""
        return asdict(self)

    @property
    def hash(self) -> str:
        """SHA-256 over the canonical JSON form, truncated to 16 hex characters."""
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def with_overrides(self, **section_updates: Dict[str, Any]) -> "Config":
        """Return a copy with individual fields replaced.

        Args:
            **section_updates: Mapping of section name to a dict of field
                updates, e.g. ``strategy={"atr_stop_mult": 3.0}``.

        Returns:
            A new Config instance.
        """
        sections = {f.name: getattr(self, f.name) for f in fields(self)}
        for section_name, updates in section_updates.items():
            if section_name not in sections:
                raise ConfigError(f"unknown configuration section {section_name!r}")
            sections[section_name] = replace(sections[section_name], **updates)
        return Config(**sections)


_SECTION_TYPES = {
    "strategy": StrategyParams,
    "portfolio": PortfolioParams,
    "costs": CostParams,
    "data": DataParams,
    "backtest": BacktestParams,
    "tax": TaxParams,
}


def config_from_dict(raw: Dict[str, Any]) -> Config:
    """Build a Config from a nested dictionary, rejecting unknown keys.

    Silently ignoring a misspelled parameter is how a backtest ends up running
    with defaults the author believed they had changed, so unknown keys are a
    hard error.

    Args:
        raw: Nested mapping of section name to field values.

    Returns:
        The resolved Config.

    Raises:
        ConfigError: If a section or field name is not recognised.
    """
    unknown_sections = set(raw) - set(_SECTION_TYPES)
    if unknown_sections:
        raise ConfigError(f"unknown configuration sections: {sorted(unknown_sections)}")

    sections = {}
    for name, cls in _SECTION_TYPES.items():
        values = raw.get(name) or {}
        if not isinstance(values, dict):
            raise ConfigError(f"section {name!r} must be a mapping, got {type(values).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ConfigError(f"unknown keys in section {name!r}: {sorted(unknown)}")
        sections[name] = cls(**values)
    return Config(**sections)


def load_config(path: str) -> Config:
    """Load a configuration from a YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        The resolved Config.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return config_from_dict(raw)


def apply_dotted_overrides(config: Config, overrides: Dict[str, str]) -> Config:
    """Apply ``section.field=value`` command line overrides.

    Values are coerced to the type declared on the dataclass field, so
    ``--set strategy.atr_stop_mult=3`` yields a float and
    ``--set backtest.debug_assertions=false`` yields a bool.

    Args:
        config: The configuration to modify.
        overrides: Mapping of dotted path to string value.

    Returns:
        A new Config instance.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for dotted, raw_value in overrides.items():
        if "." not in dotted:
            raise ConfigError(f"override {dotted!r} must be of the form section.field=value")
        section_name, field_name = dotted.split(".", 1)
        if section_name not in _SECTION_TYPES:
            raise ConfigError(f"unknown configuration section {section_name!r}")
        field_types = {f.name: f.type for f in fields(_SECTION_TYPES[section_name])}
        if field_name not in field_types:
            raise ConfigError(f"unknown field {field_name!r} in section {section_name!r}")
        grouped.setdefault(section_name, {})[field_name] = _coerce(
            raw_value, field_types[field_name]
        )
    return config.with_overrides(**grouped)


def _coerce(raw: str, declared_type: Any) -> Any:
    """Coerce a command line string to the type declared on a dataclass field.

    Args:
        raw: The string value as given on the command line.
        declared_type: The field's declared type, possibly as a string
            (dataclasses store annotations as strings under some import modes).

    Returns:
        The coerced value.
    """
    name = declared_type if isinstance(declared_type, str) else getattr(
        declared_type, "__name__", str(declared_type)
    )
    if "bool" in name:
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ConfigError(f"cannot interpret {raw!r} as a boolean")
    if "int" in name:
        return int(raw)
    if "float" in name:
        return float(raw)
    if "Optional" in name and raw.strip().lower() in ("none", "null", ""):
        return None
    return raw


def deep_copy_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep copy of a nested dictionary."""
    return copy.deepcopy(value)
