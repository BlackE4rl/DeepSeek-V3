"""Tests for the performance statistics."""

import numpy as np
import pandas as pd
import pytest

from trendfolge import metrics


def _curve(values, start="2020-01-01"):
    index = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=index)


def _equity_frame(curve: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "equity": curve,
            "cash": curve,
            "positions_value": 0.0,
            "n_positions": 0.0,
            "open_risk": 0.0,
            "regime": True,
        }
    )


def _trades(net_pnl, bars=10, entry_price=100.0, shares=10.0, r=1.0):
    return pd.DataFrame(
        {
            "symbol": ["A"] * len(net_pnl),
            "currency": ["EUR"] * len(net_pnl),
            "entry_date": pd.bdate_range("2020-01-01", periods=len(net_pnl)),
            "exit_date": pd.bdate_range("2020-02-01", periods=len(net_pnl)),
            "shares": [shares] * len(net_pnl),
            "entry_price_local": [entry_price] * len(net_pnl),
            "exit_price_local": [entry_price] * len(net_pnl),
            "fx_in": [1.0] * len(net_pnl),
            "fx_out": [1.0] * len(net_pnl),
            "gross_pnl_acct": list(net_pnl),
            "costs_acct": [2.0] * len(net_pnl),
            "net_pnl_acct": list(net_pnl),
            "r_multiple": [r] * len(net_pnl),
            "bars_held": [bars] * len(net_pnl),
            "exit_reason": ["stop"] * len(net_pnl),
            "local_return": [0.0] * len(net_pnl),
            "fx_return": [0.0] * len(net_pnl),
        }
    )


# --- return and risk ---------------------------------------------------------


def test_cagr_of_an_exact_doubling_over_one_year():
    index = pd.DatetimeIndex(["2020-01-01", "2021-01-01"])
    curve = pd.Series([100.0, 200.0], index=index)

    # 366 days in the 2020 leap year, so slightly under a full annualization.
    assert metrics.cagr(curve) == pytest.approx(2.0 ** (365.25 / 366) - 1.0)


def test_total_return_is_simple_and_cagr_needs_positive_endpoints():
    curve = _curve([100.0, 150.0])
    assert metrics.total_return(curve) == pytest.approx(0.5)

    wiped_out = _curve([100.0, 0.0])
    assert np.isnan(metrics.cagr(wiped_out))


def test_a_flat_curve_yields_zero_growth_and_undefined_ratios():
    curve = _curve([100.0] * 250)

    assert metrics.cagr(curve) == pytest.approx(0.0)
    assert metrics.annualized_volatility(metrics.daily_returns(curve)) == pytest.approx(0.0)
    assert np.isnan(metrics.sharpe_ratio(metrics.daily_returns(curve)))
    assert np.isnan(metrics.calmar_ratio(curve))


def test_max_drawdown_and_its_duration_are_measured_exactly():
    curve = _curve([100.0, 120.0, 96.0, 110.0, 130.0])

    # The peak is 120 and the trough is 96, which is a twenty percent decline.
    assert metrics.max_drawdown(curve) == pytest.approx(-0.20)
    # Underwater from the bar after the peak until the bar that exceeds it.
    assert metrics.max_drawdown_duration_days(curve) > 0


def test_drawdown_of_a_monotonically_rising_curve_is_zero():
    curve = _curve(np.linspace(100.0, 200.0, 100))

    assert metrics.max_drawdown(curve) == pytest.approx(0.0)
    assert metrics.max_drawdown_duration_days(curve) == 0
    assert metrics.ulcer_index(curve) == pytest.approx(0.0)


def test_calmar_is_growth_over_pain():
    curve = _curve([100.0, 120.0, 96.0, 150.0])

    expected = metrics.cagr(curve) / abs(metrics.max_drawdown(curve))
    assert metrics.calmar_ratio(curve) == pytest.approx(expected)


def test_sharpe_uses_the_risk_free_rate():
    rng = np.random.default_rng(4)
    curve = _curve(100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 500))))
    returns = metrics.daily_returns(curve)

    assert metrics.sharpe_ratio(returns, 0.0) > metrics.sharpe_ratio(returns, 0.05)


def test_sortino_ignores_upside_deviation():
    """Two series with the same downside but different upside must rank apart."""
    calm = _curve(100.0 * np.cumprod(np.r_[1.0, np.tile([1.01, 0.995], 200)]))
    wild = _curve(100.0 * np.cumprod(np.r_[1.0, np.tile([1.05, 0.995], 200)]))

    assert metrics.sortino_ratio(metrics.daily_returns(wild)) > metrics.sortino_ratio(
        metrics.daily_returns(calm)
    )


def test_sortino_of_a_series_without_losses_is_infinite():
    curve = _curve(100.0 * np.cumprod(np.full(100, 1.001)))

    assert metrics.sortino_ratio(metrics.daily_returns(curve)) == float("inf")


def test_monthly_and_yearly_returns_compound_back_to_the_total():
    rng = np.random.default_rng(11)
    index = pd.bdate_range("2018-01-01", periods=1000)
    curve = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, 1000))), index=index)

    monthly = metrics.monthly_returns(curve)
    compounded = float((1.0 + monthly).prod() - 1.0)

    assert compounded == pytest.approx(metrics.total_return(curve), rel=1e-9)


def test_empty_inputs_do_not_raise():
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))

    assert np.isnan(metrics.max_drawdown(empty))
    assert metrics.monthly_returns(empty).empty
    assert metrics.yearly_returns(empty).empty


# --- trade statistics --------------------------------------------------------


def test_trade_statistics_on_a_hand_built_log():
    trades = _trades([100.0, -50.0, 200.0, -50.0])

    stats = metrics.trade_statistics(trades)

    assert stats["n_trades"] == 4
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["profit_factor"] == pytest.approx(300.0 / 100.0)
    assert stats["expectancy_r"] == pytest.approx(1.0)
    assert stats["max_consecutive_losses"] == 1


def test_profit_factor_without_losses_is_infinite():
    stats = metrics.trade_statistics(_trades([10.0, 20.0]))

    assert stats["profit_factor"] == float("inf")


def test_profit_factor_without_any_profit_is_not_a_number():
    stats = metrics.trade_statistics(_trades([0.0, 0.0]))

    assert np.isnan(stats["profit_factor"])


def test_max_consecutive_losses_counts_the_longest_run():
    stats = metrics.trade_statistics(_trades([-1.0, -1.0, 5.0, -1.0, -1.0, -1.0, 5.0]))

    assert stats["max_consecutive_losses"] == 3


def test_payoff_ratio_relates_average_win_to_average_loss():
    trades = _trades([100.0, -50.0], entry_price=100.0, shares=10.0)

    stats = metrics.trade_statistics(trades)

    # Entry value is 1000, so the win is +10 % and the loss is -5 %.
    assert stats["avg_win_pct"] == pytest.approx(0.10)
    assert stats["avg_loss_pct"] == pytest.approx(-0.05)
    assert stats["payoff_ratio"] == pytest.approx(2.0)


def test_empty_trade_log_yields_nan_rather_than_raising():
    stats = metrics.trade_statistics(pd.DataFrame(columns=["net_pnl_acct"]))

    assert stats["n_trades"] == 0
    assert np.isnan(stats["hit_rate"])


def test_exit_reason_breakdown_shares_sum_to_one():
    trades = _trades([1.0, -1.0, 1.0])
    trades.loc[0, "exit_reason"] = "trend break"

    breakdown = metrics.exit_reason_breakdown(trades)

    assert breakdown["share"].sum() == pytest.approx(1.0)
    assert set(breakdown.index) == {"stop", "trend break"}


def test_currency_attribution_separates_local_and_fx_moves():
    trades = _trades([100.0, 100.0])
    trades["currency"] = ["USD", "USD"]
    trades["local_return"] = [0.05, 0.05]
    trades["fx_return"] = [0.10, 0.20]

    attribution = metrics.currency_attribution(trades)

    assert attribution.loc["USD", "avg_local_return"] == pytest.approx(0.05)
    assert attribution.loc["USD", "avg_fx_return"] == pytest.approx(0.15)


# --- exposure and costs ------------------------------------------------------


def test_exposure_statistics_read_the_position_columns():
    frame = _equity_frame(_curve([100.0] * 10))
    frame["n_positions"] = [0, 1, 2, 2, 0, 0, 3, 3, 1, 0]
    frame["positions_value"] = frame["n_positions"] * 10.0

    stats = metrics.exposure_statistics(frame)

    assert stats["max_open_positions"] == 3
    assert stats["days_with_a_position"] == pytest.approx(0.6)
    assert stats["avg_open_positions"] == pytest.approx(1.2)


def test_cost_statistics_annualize_the_drag():
    index = pd.bdate_range("2020-01-01", periods=253)
    frame = _equity_frame(pd.Series(100_000.0, index=index))

    stats = metrics.cost_statistics(
        frame, {"traded_notional": 1_000_000.0, "total": 1_000.0, "commission": 400.0,
                "slippage": 600.0}
    )

    years = (index[-1] - index[0]).days / 365.25
    assert stats["annual_turnover"] == pytest.approx(10.0 / years)
    assert stats["cost_drag_bps_per_year"] == pytest.approx(100.0 / years)
    assert stats["total_commission"] == pytest.approx(400.0)


def test_compute_metrics_returns_a_flat_dictionary():
    curve = _curve(100.0 * np.cumprod(np.full(400, 1.0005)))
    result = metrics.compute_metrics(
        _equity_frame(curve), _trades([10.0, -5.0]), 0.0, {"traded_notional": 1.0, "total": 0.5}
    )

    assert result["n_trades"] == 2
    assert result["cagr"] > 0
    assert "cost_drag_bps_per_year" in result
    assert all(not isinstance(value, (list, dict)) for value in result.values())
