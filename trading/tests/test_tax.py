"""Tests for the German capital income tax model.

The numbers here are worked out by hand rather than read off the implementation,
because this module is the one that decides whether the whole strategy is worth
running: if it is wrong in the strategy's favour, every conclusion built on it
is wrong too.
"""

import numpy as np
import pandas as pd
import pytest

from trendfolge import tax
from trendfolge.config import TaxParams

RATE = 0.26375  # 25 % Abgeltungsteuer plus 5.5 % Solidaritätszuschlag


def _flat_curve(start="2020-01-01", end="2023-12-31", value=100_000.0):
    index = pd.bdate_range(start, end)
    return pd.Series(value, index=index)


def _growing_curve(start="2018-01-01", end="2026-06-30", value=100_000.0, annual=0.08):
    index = pd.bdate_range(start, end)
    path = value * np.cumprod(np.full(len(index), (1.0 + annual) ** (1 / 252)))
    return pd.Series(path, index=index)


def _trades(entries):
    return pd.DataFrame(
        [{"exit_date": pd.Timestamp(date), "net_pnl_acct": float(pnl)} for date, pnl in entries]
    )


# --- the rate itself ---------------------------------------------------------


def test_effective_rate_without_church_tax():
    assert TaxParams().effective_rate == pytest.approx(0.26375)


def test_church_tax_reduces_the_abgeltungsteuer_base():
    """§ 32d EStG divides by 4 + k rather than simply adding the church tax."""
    with_church = TaxParams(church_tax=0.09)

    assert with_church.effective_rate == pytest.approx(0.09 / 4.09 + 1 / 4.09 * 1.055, rel=1e-9)
    assert with_church.effective_rate == pytest.approx(0.279951, abs=1e-6)
    assert TaxParams(church_tax=0.08).effective_rate == pytest.approx(0.278186, abs=1e-6)
    # Naively adding 9 % on top would overstate the burden.
    assert with_church.effective_rate < 0.26375 + 0.09


def test_unknown_years_have_no_basiszins():
    assert TaxParams().basiszins_for(2024) == pytest.approx(0.0229)
    assert TaxParams().basiszins_for(1999) == 0.0


def test_settlement_mode_is_validated():
    from trendfolge.config import ConfigError

    with pytest.raises(ConfigError, match="settlement"):
        TaxParams(settlement="sometime")


# --- directly held shares ----------------------------------------------------


def test_single_year_gain_is_taxed_after_the_allowance():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", 5000.0)]), _flat_curve(), TaxParams()
    )

    assert result.total_tax == pytest.approx(4000.0 * RATE)
    assert result.ledger["allowance_used"].iloc[0] == pytest.approx(1000.0)
    assert result.ledger["taxable"].iloc[0] == pytest.approx(4000.0)


def test_a_gain_below_the_allowance_is_untaxed_and_the_rest_expires():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", 400.0), ("2021-06-01", 400.0)]),
        _flat_curve(),
        TaxParams(),
    )

    assert result.total_tax == pytest.approx(0.0)
    # Each year gets its own allowance; the unused part does not accumulate.
    assert list(result.ledger["allowance_used"]) == pytest.approx([400.0, 400.0])


def test_losses_offset_gains_within_the_same_year():
    result = tax.tax_direct_equity(
        _trades([("2020-03-01", 6000.0), ("2020-09-01", -2000.0)]),
        _flat_curve(),
        TaxParams(),
    )

    assert result.total_tax == pytest.approx((4000.0 - 1000.0) * RATE)


def test_an_unused_loss_carries_into_the_next_year():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", -5000.0), ("2021-06-01", 8000.0)]),
        _flat_curve(),
        TaxParams(),
    )

    ledger = result.ledger.set_index("year")
    assert ledger.loc[2020, "tax"] == pytest.approx(0.0)
    assert ledger.loc[2020, "loss_carried_out"] == pytest.approx(-5000.0)
    assert ledger.loc[2021, "loss_carried_in"] == pytest.approx(-5000.0)
    # 8000 - 5000 carried loss - 1000 allowance = 2000 taxable.
    assert result.total_tax == pytest.approx(2000.0 * RATE)


def test_a_losing_year_wastes_its_allowance():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", -3000.0)]), _flat_curve(), TaxParams()
    )

    assert result.ledger["allowance_used"].iloc[0] == pytest.approx(0.0)
    assert result.total_tax == pytest.approx(0.0)


# --- collection: DEGIRO versus a German broker -------------------------------


def test_assessment_defers_the_payment_by_the_configured_lag():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", 5000.0)]),
        _flat_curve(),
        TaxParams(settlement="assessment", payment_lag_months=12),
    )

    assert result.ledger["paid_on"].iloc[0] == pd.Timestamp("2021-12-31")
    charged = result.equity.diff() < 0
    assert result.equity.index[charged.to_numpy()][0] == pd.Timestamp("2021-12-31")


def test_withholding_charges_on_the_trade_date():
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", 5000.0)]),
        _flat_curve(),
        TaxParams(settlement="withholding"),
    )

    charged = result.equity.diff() < 0
    assert result.equity.index[charged.to_numpy()][0] == pd.Timestamp("2020-06-01")


def test_a_foreign_broker_leaves_you_better_off_than_a_german_one():
    """The DEGIRO effect: untaxed gains keep compounding until the assessment.

    If this assertion ever flips, the two settlement modes are wired the wrong
    way round.
    """
    curve = _growing_curve()
    trades = _trades(
        [(f"{year}-06-01", 4000.0) for year in range(2018, 2026)]
    )

    deferred = tax.tax_direct_equity(
        trades, curve, TaxParams(settlement="assessment", payment_lag_months=12)
    )
    immediate = tax.tax_direct_equity(trades, curve, TaxParams(settlement="withholding"))

    assert deferred.total_tax == pytest.approx(immediate.total_tax, rel=1e-9)
    assert deferred.final_equity > immediate.final_equity


def test_withholding_refunds_tax_when_a_later_loss_offsets_an_earlier_gain():
    result = tax.tax_direct_equity(
        _trades([("2020-03-02", 6000.0), ("2020-09-01", -4000.0)]),
        _flat_curve(),
        TaxParams(settlement="withholding"),
    )

    # Net 2000 gain, 1000 allowance, so 1000 is taxable in the end.
    assert result.total_tax == pytest.approx(1000.0 * RATE)
    # The refund shows up as a rise in the after-tax curve on the loss date.
    assert result.equity.loc[pd.Timestamp("2020-09-01")] > result.equity.loc[
        pd.Timestamp("2020-08-31")
    ]


def test_tax_owed_after_the_last_bar_is_still_charged_and_flagged():
    """Stopping the backtest early does not make the liability disappear."""
    curve = _flat_curve("2020-01-01", "2020-12-31")
    result = tax.tax_direct_equity(
        _trades([("2020-06-01", 5000.0)]),
        curve,
        TaxParams(settlement="assessment", payment_lag_months=12),
    )

    assert bool(result.ledger["accrued_after_period"].iloc[0])
    assert result.final_equity == pytest.approx(100_000.0 - 4000.0 * RATE)


# --- accumulating fund -------------------------------------------------------


def test_vorabpauschale_follows_the_basisertrag_formula():
    curve = _growing_curve("2024-01-01", "2024-12-31", annual=0.30)
    result = tax.tax_accumulating_fund(curve, TaxParams(), sold_at_end=False)

    row = result.ledger.iloc[0]
    assert row["basisertrag"] == pytest.approx(row["value_start"] * 0.7 * 0.0229)
    assert row["vorabpauschale"] == pytest.approx(row["basisertrag"])


def test_vorabpauschale_is_capped_at_the_actual_gain():
    curve = _growing_curve("2024-01-01", "2024-12-31", annual=0.001)
    result = tax.tax_accumulating_fund(curve, TaxParams(), sold_at_end=False)

    row = result.ledger.iloc[0]
    actual_gain = row["value_end"] - row["value_start"]
    assert row["vorabpauschale"] == pytest.approx(actual_gain)
    assert row["vorabpauschale"] < row["basisertrag"]


def test_no_vorabpauschale_in_a_losing_year():
    curve = _growing_curve("2024-01-01", "2024-12-31", annual=-0.20)
    result = tax.tax_accumulating_fund(curve, TaxParams(), sold_at_end=False)

    assert result.ledger["vorabpauschale"].iloc[0] == pytest.approx(0.0)
    assert result.total_tax == pytest.approx(0.0)


def test_the_negative_basiszins_years_levy_nothing():
    curve = _growing_curve("2021-01-01", "2022-12-31", annual=0.20)
    result = tax.tax_accumulating_fund(curve, TaxParams(), sold_at_end=False)

    assert (result.ledger["basiszins"] == 0.0).all()
    assert result.total_tax == pytest.approx(0.0)


def test_partial_exemption_removes_thirty_percent_of_the_fund_gain():
    """With the allowance switched off the relation is exactly thirty percent.

    The allowance is subtracted *after* the exemption, so with it in play the
    ratio is no longer a clean 0.7 -- it differs by exactly
    ``exemption × allowance × rate``, which the second half checks.
    """
    curve = _growing_curve("2024-01-01", "2024-12-31", annual=0.50)
    no_allowance = {"annual_allowance": 0.0}

    full = tax.tax_accumulating_fund(
        curve, TaxParams(fund_partial_exemption=0.0, **no_allowance)
    )
    relieved = tax.tax_accumulating_fund(
        curve, TaxParams(fund_partial_exemption=0.30, **no_allowance)
    )
    assert relieved.total_tax == pytest.approx(full.total_tax * 0.70, rel=1e-9)

    with_allowance_full = tax.tax_accumulating_fund(curve, TaxParams(fund_partial_exemption=0.0))
    with_allowance_relieved = tax.tax_accumulating_fund(curve, TaxParams())
    assert with_allowance_relieved.total_tax == pytest.approx(
        with_allowance_full.total_tax * 0.70 - 0.30 * 1000.0 * RATE, rel=1e-9
    )


def test_the_sale_deducts_vorabpauschalen_that_were_already_taxed():
    """Otherwise the same euro of return would be taxed twice."""
    curve = _growing_curve()
    params = TaxParams()

    result = tax.tax_accumulating_fund(curve, params, sold_at_end=True)
    already_taxed = result.ledger["vorabpauschale"].dropna().sum()
    sale_row = result.ledger.iloc[-1]

    gross_gain = float(curve.iloc[-1] - curve.iloc[0])
    expected_taxable = (gross_gain - already_taxed) * (1 - params.fund_partial_exemption)
    assert sale_row["taxable"] == pytest.approx(expected_taxable)
    assert already_taxed > 0


def test_holding_on_defers_the_bill_and_reports_it_as_a_liability():
    curve = _growing_curve()
    params = TaxParams()

    sold = tax.tax_accumulating_fund(curve, params, sold_at_end=True)
    held = tax.tax_accumulating_fund(curve, params, sold_at_end=False)

    assert held.final_equity > sold.final_equity
    assert held.deferred_liability > 0
    assert sold.deferred_liability == 0.0
    # Nothing is hidden: what was not charged is exactly what is disclosed.
    assert held.final_equity - held.deferred_liability == pytest.approx(
        sold.final_equity, rel=1e-6
    )


# --- the comparison this whole module exists for -----------------------------


def test_an_accumulating_fund_beats_annual_realization_at_the_same_gross_return():
    """The structural asymmetry, stated as a test.

    Same gross path, same rate. The fund keeps 30 % of the gain out of the tax
    base and defers the rest; the directly held strategy pays in full, every
    year. This is the single largest reason an active strategy has to clear a
    high bar before it is worth running.
    """
    curve = _growing_curve()
    params = TaxParams()

    yearly = []
    for year in sorted(set(curve.index.year)):
        segment = curve[curve.index.year == year]
        yearly.append((segment.index[-1], float(segment.iloc[-1] - segment.iloc[0])))

    direct = tax.tax_direct_equity(_trades(yearly), curve, params)
    fund_sold = tax.tax_accumulating_fund(curve, params, sold_at_end=True)
    fund_held = tax.tax_accumulating_fund(curve, params, sold_at_end=False)

    assert direct.total_tax > fund_sold.total_tax
    assert fund_sold.final_equity > direct.final_equity
    assert fund_held.final_equity > fund_sold.final_equity


def test_the_funds_advantage_grows_with_the_holding_period():
    params = TaxParams()

    def gap(end: str) -> float:
        curve = _growing_curve("2018-01-01", end)
        yearly = []
        for year in sorted(set(curve.index.year)):
            segment = curve[curve.index.year == year]
            yearly.append((segment.index[-1], float(segment.iloc[-1] - segment.iloc[0])))
        direct = tax.tax_direct_equity(_trades(yearly), curve, params)
        fund = tax.tax_accumulating_fund(curve, params, sold_at_end=True)
        return fund.final_equity / direct.final_equity

    assert gap("2026-06-30") > gap("2022-06-30") > 1.0


# --- switching it off --------------------------------------------------------


def test_disabling_tax_leaves_both_curves_untouched():
    curve = _growing_curve()
    params = TaxParams(enabled=False)

    direct = tax.tax_direct_equity(_trades([("2020-06-01", 5000.0)]), curve, params)
    fund = tax.tax_accumulating_fund(curve, params)

    pd.testing.assert_series_equal(direct.equity, curve)
    pd.testing.assert_series_equal(fund.equity, curve)
    assert direct.total_tax == 0.0


def test_zero_rates_leave_the_curve_untouched():
    curve = _growing_curve()
    params = TaxParams(capital_gains_rate=0.0, solidarity_surcharge=0.0)

    result = tax.tax_direct_equity(_trades([("2020-06-01", 5000.0)]), curve, params)

    np.testing.assert_allclose(result.equity.to_numpy(), curve.to_numpy())


def test_an_empty_trade_log_costs_nothing():
    curve = _growing_curve()
    result = tax.tax_direct_equity(pd.DataFrame(columns=["exit_date", "net_pnl_acct"]),
                                   curve, TaxParams())

    pd.testing.assert_series_equal(result.equity, curve)


# --- the mechanism that applies payments to a curve --------------------------


def test_removing_money_early_costs_the_compounding_on_it():
    curve = _growing_curve("2020-01-01", "2026-12-31", annual=0.10)
    amount = 5_000.0

    early = tax.apply_payments(curve, pd.Series({pd.Timestamp("2020-06-01"): amount}))
    late = tax.apply_payments(curve, pd.Series({curve.index[-1]: amount}))

    assert late.iloc[-1] > early.iloc[-1]
    # Paid on the last bar it costs exactly its face value; paid at the start it
    # also costs everything it would have earned in between.
    assert late.iloc[-1] == pytest.approx(curve.iloc[-1] - amount, rel=1e-9)
    growth = curve.iloc[-1] / curve.loc[pd.Timestamp("2020-06-01")]
    assert early.iloc[-1] == pytest.approx(curve.iloc[-1] - amount * growth, rel=1e-6)


def test_payments_between_bars_land_on_the_next_available_bar():
    curve = _flat_curve("2020-01-01", "2020-01-31")
    # A Saturday, which is not a business day on the curve's index.
    payments = pd.Series({pd.Timestamp("2020-01-11"): 100.0})

    charged = tax.apply_payments(curve, payments)

    assert charged.loc[pd.Timestamp("2020-01-10")] == pytest.approx(100_000.0)
    assert charged.loc[pd.Timestamp("2020-01-13")] == pytest.approx(99_900.0)


def test_summarize_reports_the_share_of_the_gain_that_went_in_tax():
    curve = _growing_curve()
    result = tax.tax_direct_equity(_trades([("2020-06-01", 10_000.0)]), curve, TaxParams())

    summary = tax.summarize(result, curve)

    assert summary["total_tax"] == pytest.approx(9000.0 * RATE)
    assert 0 < summary["tax_share_of_gain"] < 1
    assert summary["final_equity_after_tax"] < summary["final_equity_pre_tax"]
