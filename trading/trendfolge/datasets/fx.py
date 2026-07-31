"""Currency conversion.

A rate is always quoted as *units of the account currency per one unit of the
foreign currency*, so converting a local amount is a plain multiplication
everywhere in the engine.

Forward-filling an FX rate across a gap is legitimate in a way that
forward-filling an equity bar is not: spot FX trades around the clock five days
a week, so the last known rate over an equity market holiday is a fair
approximation. That asymmetry is precisely why FX lives in its own class and is
never mixed into the bar handling.

Forward-filling stops at the end of the source series. A date beyond the last
quote is a hard error rather than a silently stale rate, because that situation
means the data is short, not that the rate stopped moving.
"""

import os
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from ..universe import FxPair
from .normalize import DataValidationError, normalize_index


class FxError(ValueError):
    """Raised when a required exchange rate is unavailable."""


class FxRates:
    """Exchange rates for every currency in a universe.

    Attributes:
        account_currency: The currency the ledger is denominated in.
    """

    def __init__(self, account_currency: str, rates: Dict[str, pd.Series]) -> None:
        """Initialise from raw per-currency series.

        Args:
            account_currency: Currency of the cash ledger.
            rates: Mapping of foreign currency to a series of account units per
                foreign unit. The account currency itself may be omitted and is
                added as a constant 1.0.
        """
        self.account_currency = account_currency
        self._raw: Dict[str, pd.Series] = {}
        for currency, series in rates.items():
            if currency == account_currency:
                continue
            clean = series.dropna().astype(float)
            if clean.empty:
                raise FxError(f"FX series for {currency} is empty")
            if (clean <= 0).any():
                raise FxError(f"FX series for {currency} contains non-positive rates")
            self._raw[currency] = clean
        self._frame: Optional[pd.DataFrame] = None

    @property
    def currencies(self) -> List[str]:
        """Foreign currencies covered, excluding the account currency."""
        return sorted(self._raw)

    def align(self, index: pd.DatetimeIndex) -> "FxRates":
        """Return a copy whose rates are materialised on the given index.

        Args:
            index: The master date index of the backtest.

        Returns:
            A new FxRates carrying a dense frame on ``index``.
        """
        aligned = FxRates(self.account_currency, self._raw)
        columns = {self.account_currency: pd.Series(1.0, index=index)}
        for currency, series in self._raw.items():
            reindexed = series.reindex(series.index.union(index)).ffill().reindex(index)
            # Never extrapolate past the end of the source data.
            reindexed[index > series.index[-1]] = np.nan
            columns[currency] = reindexed
        aligned._frame = pd.DataFrame(columns, index=index)
        return aligned

    @property
    def frame(self) -> pd.DataFrame:
        """The dense rate frame produced by :meth:`align`."""
        if self._frame is None:
            raise FxError("FxRates.align() must be called before the frame is used")
        return self._frame

    def series(self, currency: str) -> pd.Series:
        """Rates for one currency on the aligned index.

        Args:
            currency: The foreign currency.

        Returns:
            A series of account units per foreign unit.
        """
        frame = self.frame
        if currency not in frame.columns:
            raise FxError(
                f"no exchange rate for {currency}; universe declares "
                f"{sorted(frame.columns)}"
            )
        return frame[currency]

    def rate(self, currency: str, date: pd.Timestamp) -> float:
        """The rate for one currency on one date.

        Args:
            currency: The foreign currency.
            date: The date, which must be on the aligned index.

        Returns:
            Account currency units per one unit of ``currency``.

        Raises:
            FxError: If no rate is known for that date. A missing rate inside a
                position's life is a data problem and must not be papered over.
        """
        if currency == self.account_currency:
            return 1.0
        value = self.series(currency).get(date, np.nan)
        if not np.isfinite(value):
            raise FxError(
                f"no {currency}/{self.account_currency} rate on {date.date()}; "
                "the FX series does not cover the backtest range"
            )
        return float(value)

    def convert(self, amount: float, currency: str, date: pd.Timestamp) -> float:
        """Convert an amount from a local currency into the account currency."""
        return amount * self.rate(currency, date)


def load_fx_rates(
    data_dir: str, pairs: Iterable[FxPair], account_currency: str
) -> FxRates:
    """Load exchange rates from a data directory.

    Args:
        data_dir: Directory containing an ``fx`` subdirectory of CSV files.
        pairs: The FX pairs declared by the universe.
        account_currency: Currency of the cash ledger.

    Returns:
        The loaded FxRates, not yet aligned to a master index.
    """
    from .loader import read_price_csv  # local import keeps the module import-cycle free

    fx_dir = os.path.join(data_dir, "fx")
    if not os.path.isdir(fx_dir):
        fx_dir = data_dir

    rates: Dict[str, pd.Series] = {}
    for pair in pairs:
        if pair.currency == account_currency:
            continue
        path = os.path.join(fx_dir, f"{pair.file}.csv")
        if not os.path.exists(path):
            path = os.path.join(fx_dir, pair.file)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"no FX file for {pair.currency}; expected {pair.file}.csv in {fx_dir}"
            )
        frame, _dialect, _has_adjusted = read_price_csv(path)
        if "close" not in frame.columns:
            raise DataValidationError(f"{path} has no close column")
        series = frame["close"].astype(float)
        series.index = normalize_index(series.index, pair.file)
        series = series.dropna()
        if pair.invert:
            series = 1.0 / series
        rates[pair.currency] = series

    return FxRates(account_currency, rates)


def constant_fx(account_currency: str, currencies: Iterable[str], index: pd.DatetimeIndex,
                rate: float = 1.0) -> FxRates:
    """Build a flat FX table, used by tests that want to isolate other effects.

    Args:
        account_currency: Currency of the cash ledger.
        currencies: Foreign currencies to cover.
        index: Dates to cover.
        rate: The constant rate to use for every foreign currency.

    Returns:
        An aligned FxRates.
    """
    rates = {
        currency: pd.Series(rate, index=index)
        for currency in currencies
        if currency != account_currency
    }
    return FxRates(account_currency, rates).align(index)
