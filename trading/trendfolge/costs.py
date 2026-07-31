"""Transaction costs.

Three components are charged on both sides of every trade:

* commission, a flat fee with an optional variable part and a floor;
* half the quoted spread, because a market order crosses it;
* slippage, because a market-on-open order does not get the printed open, and a
  stop order gets a worse price still since it fills into a market that is
  already moving against it.

Costs are the difference between a backtest and a fantasy, so they are modelled
explicitly, accumulated per trade, and reported in the account currency, as a
share of starting equity and as basis points per year.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from .config import CostParams

_BPS = 1e-4


@dataclass(frozen=True)
class Execution:
    """The outcome of applying the cost model to one side of one trade.

    Attributes:
        reference_price_local: The untouched bar price the fill is derived from.
        price_local: The price actually paid or received.
        slippage_per_share_local: Difference between the two, always adverse.
    """

    reference_price_local: float
    price_local: float
    slippage_per_share_local: float


class CostModel:
    """Applies commission, spread and slippage to fills.

    Attributes:
        params: The cost parameters.
    """

    def __init__(
        self, params: CostParams, half_spread_by_symbol: Optional[Dict[str, float]] = None
    ) -> None:
        """Initialise the model.

        Args:
            params: The cost parameters.
            half_spread_by_symbol: Per-symbol overrides of the half spread, in
                basis points. Symbols absent from the mapping use the default.
        """
        self.params = params
        self._half_spread = dict(half_spread_by_symbol or {})

    def half_spread_bps(self, symbol: str) -> float:
        """Half the quoted spread for one symbol, in basis points."""
        override = self._half_spread.get(symbol)
        if override is None:
            return self.params.half_spread_bps
        # A zero-cost run must stay zero-cost even where the universe file
        # supplies a per-symbol spread.
        if self.params.half_spread_bps == 0.0:
            return 0.0
        return override

    def buy(self, symbol: str, reference_price_local: float) -> Execution:
        """Price a market-on-open purchase.

        Args:
            symbol: Ticker.
            reference_price_local: The bar's open price.

        Returns:
            The Execution, priced above the open.
        """
        adverse = (self.params.slippage_bps + self.half_spread_bps(symbol)) * _BPS
        price = reference_price_local * (1.0 + adverse)
        return Execution(reference_price_local, price, price - reference_price_local)

    def sell(
        self, symbol: str, reference_price_local: float, is_stop: bool = False
    ) -> Execution:
        """Price a sale.

        Args:
            symbol: Ticker.
            reference_price_local: The bar's open price for a market exit, or
                ``min(open, stop)`` for a stop exit.
            is_stop: Whether the sale is a stop execution, which fills worse.

        Returns:
            The Execution, priced below the reference.
        """
        adverse = (self.params.slippage_bps + self.half_spread_bps(symbol)) * _BPS
        if is_stop:
            adverse += self.params.stop_extra_slippage_bps * _BPS
        price = reference_price_local * (1.0 - adverse)
        return Execution(reference_price_local, price, reference_price_local - price)

    def commission(self, notional_acct: float) -> float:
        """Commission for one side, in account currency.

        Args:
            notional_acct: Traded value in account currency.

        Returns:
            The commission. A model with every component set to zero returns
            zero, which is what the gross-versus-net comparison relies on.
        """
        variable = self.params.commission_bps * _BPS * abs(notional_acct)
        fee = self.params.commission_fixed_acct + variable
        return max(fee, self.params.min_commission_acct)

    def estimate_round_trip_bps(self, symbol: str) -> float:
        """Rough total cost of a round trip in basis points, for reporting."""
        one_side = self.params.slippage_bps + self.half_spread_bps(symbol)
        return 2.0 * one_side + self.params.stop_extra_slippage_bps


def build_cost_model(params: CostParams, universe) -> CostModel:
    """Build a cost model, picking up per-symbol spread overrides from a universe.

    Args:
        params: The cost parameters.
        universe: The universe definition.

    Returns:
        The configured CostModel.
    """
    overrides = {
        meta.symbol: meta.half_spread_bps
        for meta in universe.all_symbols
        if meta.half_spread_bps is not None
    }
    return CostModel(params, overrides)
