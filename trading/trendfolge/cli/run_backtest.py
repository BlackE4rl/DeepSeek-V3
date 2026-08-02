"""Run one backtest and write a report, metrics, trades and figures.

The report answers three separate questions, which is why it carries more than
one benchmark:

* against an equal-weight basket of the traded universe -- did the timing rule
  add anything over simply holding the same shares?
* against the ETF you would otherwise have bought -- was running this worth the
  trouble at all?
* against that ETF held at the same average exposure -- is any lead real, or is
  it just a smaller position?

And all of it again after German tax, because an active strategy realizes gains
every year while a fund defers them.
"""

import argparse
import json
import os
from typing import Dict, List, Mapping, Tuple

import numpy as np
import pandas as pd

from .. import benchmarks, plots, report, tax
from ..config import CostParams
from ..costs import CostModel, build_cost_model
from ..engine import run_backtest
from ..metrics import compute_metrics
from ._common import add_common_arguments, is_synthetic, manifest_notes, resolve

# Instruments that are funds rather than baskets of directly held shares, and so
# get the Teilfreistellung and the deferral. Matched case-insensitively on the
# symbol; anything else is treated as directly held.
_FUND_HINTS = ("QDVE", "QQQ", "XLK", "SYN-QQQ")


def _is_fund(symbol: str) -> bool:
    """Whether a benchmark symbol is a fund for tax purposes."""
    upper = symbol.upper()
    return any(hint in upper for hint in _FUND_HINTS)


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--no-figures", action="store_true", help="skip the PNG figures")
    parser.add_argument(
        "--no-common-window",
        action="store_true",
        help="compare over each series' own history instead of the shared overlap",
    )
    args = parser.parse_args()

    config, universe, panel, manifest = resolve(args)
    os.makedirs(args.out, exist_ok=True)

    costs = build_cost_model(config.costs, universe)
    result = run_backtest(panel, config, universe, costs)
    result.data_notes = list(result.data_notes) + manifest_notes(manifest)

    metrics = compute_metrics(
        result.equity, result.trades, config.backtest.risk_free_rate, result.cost_totals
    )

    free_config = config.with_overrides(
        costs={field: 0.0 for field in CostParams.__dataclass_fields__}
    )
    gross = run_backtest(panel, free_config, universe, CostModel(CostParams.zero()))
    gross_metrics = compute_metrics(
        gross.equity, gross.trades, config.backtest.risk_free_rate, gross.cost_totals
    )

    initial_equity = config.portfolio.initial_equity
    curves: Dict[str, pd.Series] = {"Strategy": result.equity["equity"]}
    for symbol in universe.all_benchmarks:
        curve = benchmarks.buy_and_hold(panel, symbol, initial_equity, costs)
        if not curve.dropna().empty:
            curves[f"{symbol} buy & hold"] = curve
    curves["Equal weight universe"] = benchmarks.equal_weight_universe(
        panel, initial_equity, costs
    )

    # Restrict everything to the range every series actually covers.
    window = None
    shortened: List[str] = []
    if not args.no_common_window:
        window = benchmarks.common_window(curves)
        if window is not None:
            strategy_start = result.equity.index[0]
            shortened = [
                name
                for name, curve in curves.items()
                if name != "Strategy" and not curve.dropna().empty
                and curve.dropna().index[0] > strategy_start
            ]
            curves = {
                name: benchmarks.rebase(curve, window[0], window[1], initial_equity)
                for name, curve in curves.items()
            }
            curves = {name: curve for name, curve in curves.items() if not curve.empty}

    strategy_curve = curves.get("Strategy", result.equity["equity"])
    exposure = metrics.get("avg_invested_fraction")
    primary = f"{universe.benchmark_symbol} buy & hold"
    if primary in curves and exposure and np.isfinite(exposure):
        matched_name = f"{universe.benchmark_symbol} at {exposure:.0%} exposure"
        curves[matched_name] = benchmarks.exposure_matched(
            curves[primary], exposure, initial_equity, config.backtest.risk_free_rate
        )

    benchmark_metrics = {
        name: benchmarks.curve_metrics(curve, config.backtest.risk_free_rate)
        for name, curve in curves.items()
        if name != "Strategy"
    }
    relative = {
        name: benchmarks.relative_statistics(strategy_curve, curve)
        for name, curve in curves.items()
        if name != "Strategy"
    }

    after_tax, ledgers, summaries, tax_curves = _apply_tax(
        strategy_curve, result.trades, curves, config, universe
    )

    figures = []
    if not args.no_figures:
        figures = [
            plots.equity_curve(
                {k: v for k, v in list(curves.items())[:3]},
                os.path.join(args.out, "equity_curve.png"),
                args.theme,
                currency=universe.account_currency,
            ),
            plots.after_tax_comparison(
                tax_curves,
                os.path.join(args.out, "after_tax.png"),
                args.theme,
                currency=universe.account_currency,
            ),
            plots.drawdown(
                strategy_curve, os.path.join(args.out, "drawdown.png"), args.theme
            ),
            plots.annual_returns(
                strategy_curve, os.path.join(args.out, "annual_returns.png"), args.theme
            ),
            plots.exposure(result.equity, os.path.join(args.out, "exposure.png"), args.theme),
            plots.trade_r_histogram(
                result.trades, os.path.join(args.out, "trade_r_hist.png"), args.theme
            ),
        ]

    document = report.build_backtest_report(
        result,
        metrics,
        gross_metrics=gross_metrics,
        benchmark_metrics=benchmark_metrics,
        relative=relative,
        figures=figures,
        synthetic=is_synthetic(manifest),
        after_tax=after_tax,
        tax_ledgers=ledgers,
        tax_summaries=summaries,
        window=window,
        shortened_by=shortened,
    )
    report.write_report(document, os.path.join(args.out, "report.md"))

    result.equity.to_csv(os.path.join(args.out, "equity.csv"))
    result.trades.to_csv(os.path.join(args.out, "trades.csv"), index=False)
    result.rejected.to_csv(os.path.join(args.out, "rejected_orders.csv"), index=False)
    pd.DataFrame(curves).to_csv(os.path.join(args.out, "benchmark_curves.csv"))
    for name, ledger in ledgers.items():
        if ledger is not None and not ledger.empty:
            safe = name.replace(" ", "_").replace("/", "-")
            ledger.to_csv(os.path.join(args.out, f"tax_ledger_{safe}.csv"), index=False)
    with open(os.path.join(args.out, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "strategy_net": metrics,
                "strategy_gross": gross_metrics,
                "benchmarks": benchmark_metrics,
                "after_tax": after_tax,
                "tax_summaries": summaries,
                "config_hash": config.hash,
            },
            handle, indent=2, default=str,
        )

    print(f"CAGR {metrics['cagr']:.2%}   max drawdown {metrics['max_drawdown']:.2%}   "
          f"Calmar {metrics['calmar']:.2f}   trades {metrics['n_trades']}")
    for name, values in benchmark_metrics.items():
        if values:
            print(f"  vs {name}: CAGR {values['cagr']:.2%}  "
                  f"max drawdown {values['max_drawdown']:.2%}")
    print("\nafter tax:")
    for name, values in after_tax.items():
        if values:
            print(f"  {name}: final {values['final_equity']:,.0f}  CAGR {values['cagr']:.2%}")
    print(f"\nreport written to {os.path.join(args.out, 'report.md')}")
    if is_synthetic(manifest):
        print(
            "NOTE: this run used synthetic prices. The numbers above describe the "
            "generator, not any market."
        )


def _apply_tax(
    strategy_curve: pd.Series,
    trades: pd.DataFrame,
    curves: Mapping[str, pd.Series],
    config,
    universe,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, pd.DataFrame],
           Dict[str, Dict[str, float]], Dict[str, Dict[str, pd.Series]]]:
    """Tax the strategy and every fund benchmark, both sold and held.

    Args:
        strategy_curve: The strategy's pre-tax equity curve.
        trades: The strategy's completed round trips.
        curves: All pre-tax curves, keyed by label.
        config: The resolved configuration.
        universe: The universe definition.

    Returns:
        After-tax metrics, ledgers, summaries, and the curve pairs the figure
        needs.
    """
    rate = config.backtest.risk_free_rate
    after_tax: Dict[str, Dict[str, float]] = {}
    ledgers: Dict[str, pd.DataFrame] = {}
    summaries: Dict[str, Dict[str, float]] = {}
    figure_curves: Dict[str, Dict[str, pd.Series]] = {}

    strategy_tax = tax.tax_direct_equity(trades, strategy_curve, config.tax)
    after_tax["Strategy after tax"] = benchmarks.curve_metrics(strategy_tax.equity, rate)
    ledgers["Strategy (directly held shares)"] = strategy_tax.ledger
    summaries["Strategy"] = tax.summarize(strategy_tax, strategy_curve)
    figure_curves["Strategy"] = {"pre_tax": strategy_curve, "after_tax": strategy_tax.equity}

    charted = False
    for name, curve in curves.items():
        if name == "Strategy" or curve.dropna().empty:
            continue
        # Several curves can derive from the same instrument -- buy and hold and
        # the exposure-matched blend, for instance -- so they are keyed by their
        # full label. Keying by the bare ticker would silently overwrite one
        # with the other.
        if not _is_fund(name.split(" ")[0]):
            continue
        sold = tax.tax_accumulating_fund(curve, config.tax, sold_at_end=True)
        held = tax.tax_accumulating_fund(curve, config.tax, sold_at_end=False)
        after_tax[f"{name}, after tax (sold)"] = benchmarks.curve_metrics(sold.equity, rate)
        after_tax[f"{name}, after tax (held)"] = benchmarks.curve_metrics(held.equity, rate)
        ledgers[f"{name} (accumulating fund)"] = sold.ledger
        summaries[name] = tax.summarize(held, curve)
        summaries[name]["deferred_liability"] = held.deferred_liability
        if not charted and "buy & hold" in name:
            charted = True
            figure_curves[name] = {"pre_tax": curve, "after_tax": sold.equity}

    return after_tax, ledgers, summaries, figure_curves


if __name__ == "__main__":
    main()
