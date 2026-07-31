"""Cost accounting tests.

A single round trip is driven through the engine on a hand-built price path so
that every number -- share count, fill price, commission, resulting cash -- can
be recomputed independently and compared to the cent.
"""

import numpy as np
import pandas as pd
import pytest

from helpers import default_test_config, make_panel, make_universe, pullback_frame, rising_regime_frame
from trendfolge.config import CostParams
from trendfolge.costs import CostModel, build_cost_model
from trendfolge.datasets import synthetic
from trendfolge.engine import run_backtest
from trendfolge.universe import FxPair, SymbolMeta

SESSIONS = synthetic.make_sessions("2005-01-03", "2035-01-01", "XNAS")

ENTRY_ADVERSE = 1.0 + (5.0 + 3.0) * 1e-4
STOP_ADVERSE = 1.0 - (5.0 + 3.0 + 10.0) * 1e-4


def _crash_scenario(crash_factor: float = 0.75, tail: int = 6):
    """One pullback entry followed by a gap-down crash through the stop."""
    index = SESSIONS[:400]
    frame, reclaim = pullback_frame(index, n_tail=tail)
    frame = frame.iloc[: reclaim + tail + 1].copy()

    previous_close = frame["close"].iloc[-2]
    crash_open = previous_close * crash_factor
    frame.iloc[-1, frame.columns.get_loc("open")] = crash_open
    frame.iloc[-1, frame.columns.get_loc("high")] = crash_open * 1.005
    frame.iloc[-1, frame.columns.get_loc("low")] = crash_open * 0.97
    frame.iloc[-1, frame.columns.get_loc("close")] = crash_open * 0.98
    frame.iloc[-1, frame.columns.get_loc("adj_close")] = crash_open * 0.98

    used_index = frame.index
    frames = {"AAA": frame, "REGIME": rising_regime_frame(used_index)}
    universe = make_universe([SymbolMeta("AAA", "EUR", "XNAS", "A")])
    return frames, universe, reclaim, used_index


def _run(config=None):
    frames, universe, reclaim, index = _crash_scenario()
    config = config or default_test_config()
    panel = make_panel(frames, universe, config)
    result = run_backtest(panel, config, universe)
    return result, panel, frames["AAA"], reclaim, index


def test_exactly_one_round_trip_happens():
    result, _panel, _frame, _reclaim, _index = _run()

    assert len(result.trades) == 1
    assert result.trades["exit_reason"].iloc[0] == "stop"
    assert result.open_positions.empty


def test_share_count_follows_the_risk_budget():
    result, panel, _frame, reclaim, index = _run()
    config = default_test_config()

    signal_date = index[reclaim]
    atr = panel.signals["AAA"]["atr"].loc[signal_date]
    stop_distance = config.strategy.atr_stop_mult * atr
    expected = np.floor(
        config.portfolio.initial_equity * config.portfolio.risk_per_trade / stop_distance
    )

    assert result.trades["shares"].iloc[0] == pytest.approx(expected)


def test_entry_fill_and_cash_are_exact_to_the_cent():
    result, _panel, frame, reclaim, index = _run()

    fill_date = index[reclaim + 1]
    trade = result.trades.iloc[0]
    expected_price = frame["open"].loc[fill_date] * ENTRY_ADVERSE

    assert trade["entry_date"] == fill_date
    assert trade["entry_price_local"] == pytest.approx(expected_price)

    expected_cash = 100_000.0 - trade["shares"] * expected_price - 1.0
    assert result.equity.loc[fill_date, "cash"] == pytest.approx(expected_cash, abs=1e-6)


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop_level():
    result, _panel, frame, _reclaim, index = _run()

    trade = result.trades.iloc[0]
    exit_date = trade["exit_date"]
    crash_open = frame["open"].loc[exit_date]

    assert exit_date == index[-1]
    assert trade["exit_price_local"] == pytest.approx(crash_open * STOP_ADVERSE)
    # The stop level itself was far above the open, so filling there would have
    # been a fiction worth several percent of the position.
    assert trade["exit_price_local"] < trade["entry_price_local"] * 0.9


def test_final_cash_reconciles_with_both_fills():
    result, _panel, frame, reclaim, index = _run()

    trade = result.trades.iloc[0]
    entry_price = frame["open"].loc[index[reclaim + 1]] * ENTRY_ADVERSE
    exit_price = frame["open"].loc[index[-1]] * STOP_ADVERSE

    expected = (
        100_000.0
        - trade["shares"] * entry_price
        - 1.0
        + trade["shares"] * exit_price
        - 1.0
    )
    assert result.equity["cash"].iloc[-1] == pytest.approx(expected, abs=1e-6)
    assert result.equity["equity"].iloc[-1] == pytest.approx(expected, abs=1e-6)


def test_gross_minus_costs_equals_net_for_every_trade():
    result, _panel, _frame, _reclaim, _index = _run()

    trades = result.trades
    np.testing.assert_allclose(
        trades["net_pnl_acct"].to_numpy(),
        (trades["gross_pnl_acct"] - trades["costs_acct"]).to_numpy(),
        atol=1e-9,
    )


def test_reported_cost_totals_match_the_trade_log():
    result, _panel, _frame, _reclaim, _index = _run()

    assert result.cost_totals["total"] == pytest.approx(
        result.trades["costs_acct"].sum(), abs=1e-9
    )
    assert result.cost_totals["commission"] == pytest.approx(2.0)


def test_a_cost_free_run_ends_richer_than_the_costed_one():
    costed, _p, _f, _r, _i = _run()
    free_config = default_test_config(costs={
        "commission_fixed_acct": 0.0,
        "commission_bps": 0.0,
        "min_commission_acct": 0.0,
        "slippage_bps": 0.0,
        "half_spread_bps": 0.0,
        "stop_extra_slippage_bps": 0.0,
    })
    free, _p2, _f2, _r2, _i2 = _run(free_config)

    assert free.cost_totals["total"] == pytest.approx(0.0)
    assert free.equity["equity"].iloc[-1] > costed.equity["equity"].iloc[-1]
    # With zero friction the gross and net figures coincide.
    assert free.trades["net_pnl_acct"].iloc[0] == pytest.approx(
        free.trades["gross_pnl_acct"].iloc[0]
    )


def test_doubling_the_slippage_doubles_the_logged_slippage():
    single, _p, _f, _r, _i = _run()
    doubled_config = default_test_config(
        costs={"slippage_bps": 10.0, "half_spread_bps": 6.0, "stop_extra_slippage_bps": 20.0}
    )
    doubled, _p2, _f2, _r2, _i2 = _run(doubled_config)

    # The share count is decided from the signal close and the ATR, neither of
    # which depends on the cost model, so the position size is unchanged and the
    # slippage scales exactly.
    assert doubled.trades["shares"].iloc[0] == single.trades["shares"].iloc[0]
    assert doubled.cost_totals["slippage"] == pytest.approx(
        2.0 * single.cost_totals["slippage"], rel=1e-3
    )


# --- currency decomposition --------------------------------------------------


def _foreign_currency_run(fx_step: float, step_after: int):
    """The same round trip, but with the symbol quoted in a foreign currency."""
    frames, _universe, reclaim, index = _crash_scenario()
    universe = make_universe(
        [SymbolMeta("AAA", "USD", "XNAS", "A")],
        fx_pairs=[FxPair("USD", "EURUSD=X", invert=False)],
    )
    rates = pd.Series(1.0, index=index)
    rates.iloc[reclaim + step_after :] = fx_step

    config = default_test_config()
    panel = make_panel(frames, universe, config, fx_rates={"USD": rates})
    return run_backtest(panel, config, universe), frames["AAA"], index, reclaim


def test_a_pure_exchange_rate_move_shows_up_as_fx_return_not_as_skill():
    result, _frame, _index, _reclaim = _foreign_currency_run(fx_step=1.10, step_after=2)

    trade = result.trades.iloc[0]
    assert trade["fx_in"] == pytest.approx(1.0)
    assert trade["fx_out"] == pytest.approx(1.10)
    assert trade["fx_return"] == pytest.approx(0.10)


def test_gross_profit_decomposes_into_local_and_currency_components():
    result, frame, index, reclaim = _foreign_currency_run(fx_step=1.10, step_after=2)

    trade = result.trades.iloc[0]
    entry_reference = frame["open"].loc[index[reclaim + 1]]
    exit_reference = frame["open"].loc[index[-1]]

    expected_gross = trade["shares"] * (
        exit_reference * trade["fx_out"] - entry_reference * trade["fx_in"]
    )
    assert trade["gross_pnl_acct"] == pytest.approx(expected_gross)

    # The reported components multiply back to the total return on the fills.
    combined = (1.0 + trade["local_return"]) * (1.0 + trade["fx_return"]) - 1.0
    realized = (
        trade["exit_price_local"] * trade["fx_out"]
    ) / (trade["entry_price_local"] * trade["fx_in"]) - 1.0
    assert combined == pytest.approx(realized)


def test_a_constant_exchange_rate_contributes_nothing():
    result, _frame, _index, _reclaim = _foreign_currency_run(fx_step=1.0, step_after=2)

    assert result.trades["fx_return"].iloc[0] == pytest.approx(0.0)


# --- direct unit tests of the cost model ------------------------------------


def test_commission_floor_binds_on_a_small_order():
    model = CostModel(
        CostParams(commission_fixed_acct=0.1, commission_bps=1.0, min_commission_acct=1.0)
    )

    assert model.commission(100.0) == pytest.approx(1.0)
    assert model.commission(1_000_000.0) == pytest.approx(0.1 + 100.0)


def test_zero_cost_model_charges_nothing():
    model = CostModel(CostParams.zero())

    assert model.commission(1_000_000.0) == 0.0
    assert model.buy("X", 100.0).price_local == pytest.approx(100.0)
    assert model.sell("X", 100.0, is_stop=True).price_local == pytest.approx(100.0)


def test_buys_pay_up_and_sells_receive_less():
    model = CostModel(CostParams(slippage_bps=5.0, half_spread_bps=3.0))

    buy = model.buy("X", 100.0)
    sell = model.sell("X", 100.0)

    assert buy.price_local == pytest.approx(100.0 * (1 + 8e-4))
    assert sell.price_local == pytest.approx(100.0 * (1 - 8e-4))
    assert buy.slippage_per_share_local > 0
    assert sell.slippage_per_share_local > 0


def test_stop_sales_fill_worse_than_market_sales():
    model = CostModel(CostParams(slippage_bps=5.0, half_spread_bps=3.0,
                                 stop_extra_slippage_bps=10.0))

    assert model.sell("X", 100.0, is_stop=True).price_local < model.sell("X", 100.0).price_local


def test_per_symbol_spread_override_is_used():
    model = CostModel(CostParams(half_spread_bps=3.0), {"WIDE": 25.0})

    assert model.half_spread_bps("WIDE") == 25.0
    assert model.half_spread_bps("NARROW") == 3.0


def test_a_zero_cost_run_ignores_per_symbol_spread_overrides():
    """Otherwise the gross-versus-net comparison would not actually be gross."""
    model = CostModel(CostParams.zero(), {"WIDE": 25.0})

    assert model.half_spread_bps("WIDE") == 0.0


def test_build_cost_model_picks_up_universe_overrides():
    universe = make_universe([SymbolMeta("AAA", "EUR", "XNAS", "A", half_spread_bps=7.5)])
    model = build_cost_model(CostParams(), universe)

    assert model.half_spread_bps("AAA") == 7.5
