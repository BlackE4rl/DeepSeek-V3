"""German capital income tax for a private investor.

Two regimes are modelled, because they differ enough to decide whether running
an active strategy is worth the trouble at all.

**Directly held shares** -- what this strategy trades. Every realized gain is
taxable in the year it is realized, at the full rate. There is no
Teilfreistellung: that relief belongs to fund units, not to shares held in your
own name. Losses from share sales offset gains from share sales and carry
forward when they cannot be used (§ 20 Abs. 6 EStG).

**An accumulating equity fund** -- what an ETF such as A142N1 is. Thirty percent
of the return is exempt, and the tax on the price gain is deferred until the
units are sold. Until then only the Vorabpauschale is due, a small fictitious
distribution computed from the Basiszins and capped at the year's actual gain.

Collection matters as much as the rate. A German broker withholds at every
realizing trade. A foreign broker such as DEGIRO withholds nothing: the gains go
into the annual return and the bill arrives with the assessment, months later.
The untaxed gain keeps compounding in the meantime, which is worth real money
and is modelled explicitly via ``TaxParams.settlement``.

How the tax is applied to an equity curve
-----------------------------------------
The after-tax curve is built by compounding the *pre-tax returns* and
subtracting each payment on the day it falls due:

    after_tax[t] = after_tax[t-1] * (1 + r_pretax[t]) - payment[t]

This captures the part that actually hurts -- money taken out stops compounding
-- without re-running the whole simulation. It assumes the strategy's percentage
returns do not depend on account size, which holds here apart from whole-share
rounding and the fixed commission per order. Re-running the engine with tax
deducted inside the loop would also change *which* trades happen, and would
answer a different question than "what did this track record cost in tax".
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import TaxParams

DIRECT_LEDGER_COLUMNS = [
    "year",
    "realized_gain",
    "loss_carried_in",
    "loss_carried_out",
    "allowance_used",
    "taxable",
    "tax",
    "paid_on",
    "accrued_after_period",
]

FUND_LEDGER_COLUMNS = [
    "year",
    "value_start",
    "value_end",
    "basiszins",
    "basisertrag",
    "vorabpauschale",
    "allowance_used",
    "taxable",
    "tax",
    "paid_on",
    "accrued_after_period",
]


@dataclass
class TaxResult:
    """The outcome of applying tax to one equity curve.

    Attributes:
        equity: The after-tax equity curve.
        ledger: One row per tax year.
        total_tax: Everything actually charged over the period.
        deferred_liability: Tax that is owed but has not been charged to the
            curve, because the position was not sold. Reported, never hidden.
    """

    equity: pd.Series
    ledger: pd.DataFrame
    total_tax: float = 0.0
    deferred_liability: float = 0.0

    @property
    def final_equity(self) -> float:
        """Account value on the last bar, after tax."""
        return float(self.equity.iloc[-1])


def payment_date(year: int, params: TaxParams) -> pd.Timestamp:
    """When the tax for one tax year is actually paid.

    Args:
        year: The tax year.
        params: Tax parameters.

    Returns:
        The end of the tax year for a withholding broker -- the individual
        trades are charged on their own dates in that mode, so this is only a
        fallback -- and ``payment_lag_months`` after the end of the year for a
        foreign broker whose gains go through the annual return.
    """
    year_end = pd.Timestamp(year=year, month=12, day=31)
    if params.settlement == "withholding":
        return year_end
    return year_end + pd.DateOffset(months=params.payment_lag_months)


def apply_payments(equity: pd.Series, payments: pd.Series) -> pd.Series:
    """Compound the pre-tax returns while removing each payment when it falls due.

    Args:
        equity: The pre-tax equity curve.
        payments: Amounts to remove, indexed by date. Dates beyond the end of
            the curve are charged on the last bar, because the liability exists
            even though the period stopped.

    Returns:
        The after-tax curve, on the same index.
    """
    if equity.empty:
        return equity.copy()

    charged = _snap_to_index(payments, equity.index)
    returns = equity.pct_change().fillna(0.0).to_numpy()
    charges = charged.reindex(equity.index).fillna(0.0).to_numpy()

    values = np.empty(len(equity), dtype=float)
    current = float(equity.iloc[0]) - charges[0]
    values[0] = current
    for position in range(1, len(equity)):
        current = current * (1.0 + returns[position]) - charges[position]
        values[position] = current
    return pd.Series(values, index=equity.index, name=equity.name)


def tax_direct_equity(
    trades: pd.DataFrame, equity: pd.Series, params: TaxParams
) -> TaxResult:
    """Tax a directly held share strategy.

    Args:
        trades: The completed round trips, with ``exit_date`` and
            ``net_pnl_acct``.
        equity: The pre-tax equity curve.
        params: Tax parameters.

    Returns:
        The TaxResult.
    """
    if not params.enabled or trades.empty or equity.empty:
        return TaxResult(equity=equity.copy(), ledger=pd.DataFrame(columns=DIRECT_LEDGER_COLUMNS))

    if params.settlement == "withholding":
        payments, ledger = _direct_withholding(trades, params, equity.index[-1])
    else:
        payments, ledger = _direct_assessment(trades, params, equity.index[-1])

    after_tax = apply_payments(equity, payments)
    return TaxResult(
        equity=after_tax,
        ledger=ledger,
        total_tax=float(ledger["tax"].sum()) if not ledger.empty else 0.0,
    )


def tax_accumulating_fund(
    equity: pd.Series, params: TaxParams, sold_at_end: bool = True
) -> TaxResult:
    """Tax an accumulating equity fund held for the whole period.

    Args:
        equity: The pre-tax equity curve of the fund position.
        params: Tax parameters.
        sold_at_end: Whether the units are sold on the last bar. When false the
            deferred tax is reported as a liability instead of being charged,
            which is the realistic case for a long-term holder and the most
            favourable honest treatment of the fund.

    Returns:
        The TaxResult.
    """
    if not params.enabled or equity.empty:
        return TaxResult(equity=equity.copy(), ledger=pd.DataFrame(columns=FUND_LEDGER_COLUMNS))

    exemption = 1.0 - params.fund_partial_exemption
    rate = params.effective_rate
    cost_basis = float(equity.iloc[0])

    rows: List[Dict[str, object]] = []
    payments: Dict[pd.Timestamp, float] = {}
    taxed_vorabpauschale = 0.0
    last_date = equity.index[-1]

    for year, segment in equity.groupby(equity.index.year):
        value_start = float(segment.iloc[0])
        value_end = float(segment.iloc[-1])
        gain = value_end - value_start
        basiszins = params.basiszins_for(int(year))
        basisertrag = value_start * 0.7 * basiszins

        # No gain, no Vorabpauschale; and never more than the year actually made.
        vorabpauschale = max(0.0, min(basisertrag, gain))
        taxable_gross = vorabpauschale * exemption
        allowance_used = min(taxable_gross, params.annual_allowance)
        taxable = max(0.0, taxable_gross - allowance_used)
        tax = taxable * rate

        due = payment_date(int(year), params)
        accrued = due > last_date
        if tax:
            payments[due] = payments.get(due, 0.0) + tax
        taxed_vorabpauschale += vorabpauschale

        rows.append(
            {
                "year": int(year),
                "value_start": value_start,
                "value_end": value_end,
                "basiszins": basiszins,
                "basisertrag": basisertrag,
                "vorabpauschale": vorabpauschale,
                "allowance_used": allowance_used,
                "taxable": taxable,
                "tax": tax,
                "paid_on": due,
                "accrued_after_period": accrued,
            }
        )

    # On sale, the Vorabpauschalen already taxed raise the acquisition cost, so
    # the same euro is not taxed twice.
    final_value = float(equity.iloc[-1])
    sale_gain = final_value - cost_basis - taxed_vorabpauschale
    sale_taxable = max(0.0, sale_gain) * exemption
    sale_tax = sale_taxable * rate

    ledger = pd.DataFrame(rows, columns=FUND_LEDGER_COLUMNS)
    deferred = 0.0
    if sold_at_end and sale_tax:
        due = payment_date(int(equity.index[-1].year), params)
        payments[due] = payments.get(due, 0.0) + sale_tax
        ledger = pd.concat(
            [
                ledger,
                pd.DataFrame(
                    [
                        {
                            "year": int(equity.index[-1].year),
                            "value_start": cost_basis,
                            "value_end": final_value,
                            "basiszins": float("nan"),
                            "basisertrag": float("nan"),
                            "vorabpauschale": float("nan"),
                            "allowance_used": 0.0,
                            "taxable": sale_taxable,
                            "tax": sale_tax,
                            "paid_on": due,
                            "accrued_after_period": due > last_date,
                        }
                    ],
                    columns=FUND_LEDGER_COLUMNS,
                ),
            ],
            ignore_index=True,
        )
    elif not sold_at_end:
        deferred = sale_tax

    after_tax = apply_payments(equity, pd.Series(payments, dtype=float))
    return TaxResult(
        equity=after_tax,
        ledger=ledger,
        total_tax=float(ledger["tax"].sum()) if not ledger.empty else 0.0,
        deferred_liability=deferred,
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _direct_assessment(
    trades: pd.DataFrame, params: TaxParams, last_date: pd.Timestamp
) -> Tuple[pd.Series, pd.DataFrame]:
    """Aggregate per tax year and pay once, with the configured lag."""
    rate = params.effective_rate
    exits = pd.to_datetime(trades["exit_date"])
    by_year = trades.groupby(exits.dt.year)["net_pnl_acct"].sum()

    rows: List[Dict[str, object]] = []
    payments: Dict[pd.Timestamp, float] = {}
    carried = 0.0

    for year, realized in by_year.sort_index().items():
        realized = float(realized)
        net = realized + carried
        loss_in = carried
        if net <= 0:
            carried = net
            allowance_used = 0.0
            taxable = 0.0
            tax = 0.0
        else:
            carried = 0.0
            allowance_used = min(net, params.annual_allowance)
            taxable = max(0.0, net - allowance_used)
            tax = taxable * rate

        due = payment_date(int(year), params)
        if tax:
            payments[due] = payments.get(due, 0.0) + tax

        rows.append(
            {
                "year": int(year),
                "realized_gain": realized,
                "loss_carried_in": loss_in,
                "loss_carried_out": carried,
                "allowance_used": allowance_used,
                "taxable": taxable,
                "tax": tax,
                "paid_on": due,
                "accrued_after_period": due > last_date,
            }
        )

    return pd.Series(payments, dtype=float), pd.DataFrame(rows, columns=DIRECT_LEDGER_COLUMNS)


def _direct_withholding(
    trades: pd.DataFrame, params: TaxParams, last_date: pd.Timestamp
) -> Tuple[pd.Series, pd.DataFrame]:
    """Deduct at every realizing trade, the way a German broker does.

    A loss booked later in the same year releases tax that was already withheld
    on earlier gains, which is what the Verlustverrechnungstopf achieves in
    practice.
    """
    rate = params.effective_rate
    ordered = trades.assign(_exit=pd.to_datetime(trades["exit_date"])).sort_values("_exit")

    payments: Dict[pd.Timestamp, float] = {}
    rows: List[Dict[str, object]] = []

    current_year: Optional[int] = None
    loss_pot = 0.0
    allowance_left = params.annual_allowance
    taxed_base = 0.0
    year_totals = {"realized": 0.0, "allowance": 0.0, "taxable": 0.0, "tax": 0.0}
    loss_in = 0.0

    def close_year() -> None:
        rows.append(
            {
                "year": current_year,
                "realized_gain": year_totals["realized"],
                "loss_carried_in": loss_in,
                "loss_carried_out": -loss_pot,
                "allowance_used": year_totals["allowance"],
                "taxable": year_totals["taxable"],
                "tax": year_totals["tax"],
                "paid_on": payment_date(int(current_year), params),
                "accrued_after_period": False,
            }
        )

    for _, trade in ordered.iterrows():
        date = trade["_exit"]
        year = int(date.year)
        if current_year is None:
            current_year = year
        elif year != current_year:
            close_year()
            current_year = year
            allowance_left = params.annual_allowance
            taxed_base = 0.0
            loss_in = -loss_pot
            year_totals = {"realized": 0.0, "allowance": 0.0, "taxable": 0.0, "tax": 0.0}

        pnl = float(trade["net_pnl_acct"])
        year_totals["realized"] += pnl

        if pnl >= 0:
            offset = min(pnl, loss_pot)
            loss_pot -= offset
            remainder = pnl - offset
            used = min(remainder, allowance_left)
            allowance_left -= used
            taxable = remainder - used
            year_totals["allowance"] += used
            year_totals["taxable"] += taxable
            if taxable:
                tax = taxable * rate
                taxed_base += taxable
                year_totals["tax"] += tax
                payments[date] = payments.get(date, 0.0) + tax
        else:
            loss = -pnl
            # Release tax already withheld this year, up to the taxed base.
            released = min(loss, taxed_base)
            taxed_base -= released
            if released:
                refund = released * rate
                year_totals["taxable"] -= released
                year_totals["tax"] -= refund
                payments[date] = payments.get(date, 0.0) - refund
            loss_pot += loss - released

    if current_year is not None:
        close_year()

    return pd.Series(payments, dtype=float), pd.DataFrame(rows, columns=DIRECT_LEDGER_COLUMNS)


def _snap_to_index(payments: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Move each payment onto the first index date that is not before it.

    Payments falling after the end of the curve are charged on the last bar: the
    money is owed regardless of where the backtest happened to stop, and the
    ledger flags those rows as accrued rather than settled.

    Args:
        payments: Amounts indexed by their due date.
        index: The equity curve's index.

    Returns:
        The payments, aggregated onto dates that exist in ``index``.
    """
    if payments.empty:
        return pd.Series(0.0, index=index)

    positions = index.searchsorted(pd.DatetimeIndex(payments.index), side="left")
    positions = np.clip(positions, 0, len(index) - 1)
    snapped = pd.Series(payments.to_numpy(dtype=float), index=index[positions])
    return snapped.groupby(level=0).sum()


def summarize(result: TaxResult, pre_tax: pd.Series) -> Dict[str, float]:
    """Headline figures comparing a curve before and after tax.

    Args:
        result: The tax result.
        pre_tax: The corresponding pre-tax curve.

    Returns:
        A dictionary with the totals and the drag the tax imposed.
    """
    if pre_tax.empty:
        return {}
    gross_gain = float(pre_tax.iloc[-1] - pre_tax.iloc[0])
    net_gain = float(result.equity.iloc[-1] - result.equity.iloc[0])
    return {
        "total_tax": result.total_tax,
        "deferred_liability": result.deferred_liability,
        "gross_gain": gross_gain,
        "net_gain": net_gain,
        "tax_share_of_gain": result.total_tax / gross_gain if gross_gain > 0 else float("nan"),
        "final_equity_pre_tax": float(pre_tax.iloc[-1]),
        "final_equity_after_tax": result.final_equity,
    }
