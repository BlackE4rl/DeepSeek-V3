"""Run one backtest and write a report, metrics, trades and figures."""

import argparse
import json
import os
from typing import Dict

from .. import benchmarks, plots, report
from ..config import CostParams
from ..costs import CostModel, build_cost_model
from ..engine import run_backtest
from ..metrics import compute_metrics
from ._common import add_common_arguments, is_synthetic, manifest_notes, resolve


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument(
        "--no-figures", action="store_true", help="skip the PNG figures"
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
    benchmark_curves = {}
    benchmark_symbol = config.backtest.benchmark_symbol or universe.benchmark_symbol
    if benchmark_symbol:
        benchmark_curves[f"{benchmark_symbol} buy & hold"] = benchmarks.buy_and_hold(
            panel, benchmark_symbol, initial_equity, costs
        )
    benchmark_curves["Equal weight universe"] = benchmarks.equal_weight_universe(
        panel, initial_equity, costs
    )

    benchmark_metrics: Dict[str, Dict[str, float]] = {}
    relative: Dict[str, Dict[str, float]] = {}
    for name, curve in benchmark_curves.items():
        if curve.dropna().empty:
            continue
        frame = result.equity.copy()
        frame["equity"] = curve
        frame["positions_value"] = curve
        frame["cash"] = 0.0
        benchmark_metrics[name] = compute_metrics(
            frame.assign(n_positions=1.0), result.trades.iloc[0:0],
            config.backtest.risk_free_rate, None
        )
        relative[name] = benchmarks.relative_statistics(result.equity["equity"], curve)

    figures = []
    if not args.no_figures:
        curves = {"Strategy": result.equity["equity"], **benchmark_curves}
        figures = [
            plots.equity_curve(
                curves,
                os.path.join(args.out, "equity_curve.png"),
                args.theme,
                currency=universe.account_currency,
            ),
            plots.drawdown(
                result.equity["equity"], os.path.join(args.out, "drawdown.png"), args.theme
            ),
            plots.annual_returns(
                result.equity["equity"], os.path.join(args.out, "annual_returns.png"),
                args.theme,
            ),
            plots.exposure(
                result.equity, os.path.join(args.out, "exposure.png"), args.theme
            ),
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
    )
    report.write_report(document, os.path.join(args.out, "report.md"))

    result.equity.to_csv(os.path.join(args.out, "equity.csv"))
    result.trades.to_csv(os.path.join(args.out, "trades.csv"), index=False)
    result.rejected.to_csv(os.path.join(args.out, "rejected_orders.csv"), index=False)
    with open(os.path.join(args.out, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"strategy_net": metrics, "strategy_gross": gross_metrics,
             "benchmarks": benchmark_metrics, "config_hash": config.hash},
            handle, indent=2, default=str,
        )

    print(f"CAGR {metrics['cagr']:.2%}   max drawdown {metrics['max_drawdown']:.2%}   "
          f"Calmar {metrics['calmar']:.2f}   trades {metrics['n_trades']}")
    for name, values in benchmark_metrics.items():
        print(f"  vs {name}: CAGR {values['cagr']:.2%}  "
              f"max drawdown {values['max_drawdown']:.2%}")
    print(f"report written to {os.path.join(args.out, 'report.md')}")
    if is_synthetic(manifest):
        print(
            "NOTE: this run used synthetic prices. The numbers above describe the "
            "generator, not any market."
        )


if __name__ == "__main__":
    main()
