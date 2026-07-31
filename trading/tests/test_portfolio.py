"""Portfolio, sizing and ledger invariants."""

import numpy as np
import pandas as pd
import pytest

from trendfolge.config import PortfolioParams
from trendfolge.datasets.panel import load_panel
from trendfolge.engine import run_backtest
from trendfolge.portfolio import LedgerError, Portfolio, size_position


@pytest.fixture(scope="module")
def _panel_cache():
    return {}


def _panel(synth_dir, universe, config, cache):
    if "panel" not in cache:
        cache["panel"] = load_panel(synth_dir, universe, config)
    return cache["panel"]


# --- invariants over a full run ---------------------------------------------


def test_position_limit_is_never_exceeded(synth_dir, synth_universe, synth_config, _panel_cache):
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    result = run_backtest(panel, synth_config, synth_universe)

    assert result.equity["n_positions"].max() <= synth_config.portfolio.max_positions
    assert result.equity["n_positions"].max() >= 2, "the limit must actually be approached"


def test_equity_equals_cash_plus_positions_on_every_bar(
    synth_dir, synth_universe, synth_config, _panel_cache
):
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    result = run_backtest(panel, synth_config, synth_universe)

    residual = (
        result.equity["equity"] - result.equity["cash"] - result.equity["positions_value"]
    ).abs()
    assert residual.max() < 1e-6


def test_cash_never_goes_negative(synth_dir, synth_universe, synth_config, _panel_cache):
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    result = run_backtest(panel, synth_config, synth_universe)

    assert result.equity["cash"].min() >= -1e-9


def test_cash_stays_solvent_even_with_an_absurd_risk_budget(
    synth_dir, synth_universe, synth_config, _panel_cache
):
    """A 50 % risk budget must be truncated by cash, not borrowed."""
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    reckless = synth_config.with_overrides(portfolio={"risk_per_trade": 0.5})

    result = run_backtest(panel, reckless, synth_universe)

    assert result.equity["cash"].min() >= -1e-9
    assert result.equity["n_positions"].max() <= reckless.portfolio.max_positions


def test_order_truncations_and_rejections_are_logged(
    synth_dir, synth_universe, synth_config, _panel_cache
):
    """A strategy that only works with impossible size must say so in the report."""
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    reckless = synth_config.with_overrides(
        portfolio={"risk_per_trade": 0.5, "max_position_weight": 1.0, "max_portfolio_heat": 5.0}
    )

    result = run_backtest(panel, reckless, synth_universe)

    assert not result.rejected.empty
    assert set(result.rejected["reason"]) & {"cash truncated", "insufficient cash"}


def test_a_tighter_heat_cap_reduces_concurrent_exposure(
    synth_dir, synth_universe, synth_config, _panel_cache
):
    """Portfolio heat gates new entries; it is not a continuous hard limit.

    Once a position is open its stop ratchets up more slowly than a fast rally
    lifts the price, so its open risk can grow after the fact. The cap therefore
    constrains what may be *added*, which is what this test pins down.
    """
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    loose = synth_config.with_overrides(portfolio={"max_portfolio_heat": 1.0})
    tight = synth_config.with_overrides(portfolio={"max_portfolio_heat": 0.01})

    loose_result = run_backtest(panel, loose, synth_universe)
    tight_result = run_backtest(panel, tight, synth_universe)

    assert (
        tight_result.equity["n_positions"].mean()
        < loose_result.equity["n_positions"].mean()
    )
    assert (tight_result.rejected["reason"] == "portfolio heat").any()


def test_final_equity_equals_start_plus_realized_plus_unrealized(
    synth_dir, synth_universe, synth_config, _panel_cache
):
    """An independent reconstruction of the account value from the trade log.

    Nothing here reads the engine's cash figure: the realized part comes from the
    trade records and the unrealized part is rebuilt from the open positions and
    the panel's own mark prices. If the ledger dropped or double counted a
    commission anywhere, the two sides part company.
    """
    panel = _panel(synth_dir, synth_universe, synth_config, _panel_cache)
    result = run_backtest(panel, synth_config, synth_universe)

    realized = result.trades["net_pnl_acct"].sum()

    last_date = panel.master_index[-1]
    unrealized = 0.0
    for _, position in result.open_positions.iterrows():
        rate = panel.fx.rate(position["currency"], last_date)
        mark = panel.px_mark.loc[last_date, position["symbol"]]
        unrealized += (
            position["shares"] * (mark * rate - position["entry_price_local"] * position["entry_fx"])
            - position["entry_commission_acct"]
        )

    assert not result.open_positions.empty or realized != 0.0
    assert result.final_equity == pytest.approx(
        synth_config.portfolio.initial_equity + realized + unrealized, rel=1e-9
    )


# --- ledger unit tests -------------------------------------------------------


def test_ledger_detects_an_unrecorded_cash_movement():
    portfolio = Portfolio(PortfolioParams())
    portfolio.check_ledger()

    portfolio.cash -= 100.0  # a mutation that bypassed the flow log
    with pytest.raises(LedgerError, match="does not match"):
        portfolio.check_ledger()


def test_ledger_detects_negative_cash():
    portfolio = Portfolio(PortfolioParams(initial_equity=100.0))
    portfolio._apply_cash_flow(-200.0)

    with pytest.raises(LedgerError, match="negative"):
        portfolio.check_ledger()


# --- position sizing unit tests ---------------------------------------------


def test_risk_budget_determines_the_size_in_the_normal_case():
    params = PortfolioParams(
        initial_equity=100_000.0,
        risk_per_trade=0.01,
        max_position_weight=1.0,
        max_adv_participation=1.0,
    )
    result = size_position(
        equity_acct=100_000.0,
        close_local=50.0,
        atr_local=2.0,
        adv_shares=10_000_000.0,
        fx_rate=1.0,
        stop_multiple=2.5,
        params=params,
    )

    # 1000 of risk over a 5.00 stop distance is 200 shares.
    assert result.shares == 200
    assert result.stop_distance_local == pytest.approx(5.0)
    assert result.binding_constraint == "risk"


def test_notional_cap_binds_on_a_low_volatility_symbol():
    params = PortfolioParams(risk_per_trade=0.10, max_position_weight=0.25,
                             max_adv_participation=1.0)
    result = size_position(100_000.0, 50.0, 0.10, 1e9, 1.0, 2.5, params)

    assert result.binding_constraint == "notional weight"
    assert result.shares == np.floor(100_000.0 * 0.25 / 50.0)


def test_liquidity_cap_binds_on_a_thin_symbol():
    params = PortfolioParams(risk_per_trade=0.10, max_position_weight=1.0,
                             max_adv_participation=0.05)
    result = size_position(100_000.0, 50.0, 1.0, 1_000.0, 1.0, 2.5, params)

    assert result.binding_constraint == "liquidity"
    assert result.shares == 50


def test_exchange_rate_scales_the_size():
    params = PortfolioParams(risk_per_trade=0.01, max_position_weight=1.0,
                             max_adv_participation=1.0)
    at_par = size_position(100_000.0, 50.0, 2.0, 1e9, 1.0, 2.5, params)
    expensive = size_position(100_000.0, 50.0, 2.0, 1e9, 2.0, 2.5, params)

    assert expensive.shares == pytest.approx(at_par.shares / 2)


def test_fractional_shares_are_off_by_default_and_can_be_enabled():
    whole = PortfolioParams(risk_per_trade=0.01, max_position_weight=1.0,
                            max_adv_participation=1.0)
    fractional = PortfolioParams(risk_per_trade=0.01, max_position_weight=1.0,
                                 max_adv_participation=1.0, allow_fractional_shares=True)

    assert size_position(100_000.0, 50.0, 2.1, 1e9, 1.0, 2.5, whole).shares == 190
    assert size_position(100_000.0, 50.0, 2.1, 1e9, 1.0, 2.5, fractional).shares == pytest.approx(
        1000.0 / 5.25
    )


def test_degenerate_inputs_produce_no_position():
    params = PortfolioParams()

    assert size_position(100_000.0, 50.0, 0.0, 1e9, 1.0, 2.5, params).shares == 0
    assert size_position(100_000.0, 50.0, np.nan, 1e9, 1.0, 2.5, params).shares == 0
    assert size_position(100_000.0, 0.0, 2.0, 1e9, 1.0, 2.5, params).shares == 0
    assert size_position(100_000.0, 50.0, 2.0, 0.0, 1.0, 2.5, params).shares == 0


def test_open_risk_is_clamped_at_zero_once_the_stop_is_above_the_entry():
    from trendfolge.portfolio import Position

    position = Position(
        symbol="A",
        currency="EUR",
        shares=100.0,
        entry_date=pd.Timestamp("2020-01-02"),
        entry_price_local=50.0,
        entry_reference_local=50.0,
        entry_fx=1.0,
        initial_stop_local=45.0,
        stop_local=60.0,
        atr_at_entry=2.0,
        highest_close=65.0,
    )

    assert position.initial_risk_acct == pytest.approx(500.0)
    assert position.current_risk_acct(55.0, 1.0) == 0.0
    assert position.current_risk_acct(70.0, 1.0) == pytest.approx(1000.0)
