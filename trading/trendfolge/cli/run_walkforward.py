"""Run the walk-forward validation and write its report."""

import argparse
import json
import os

from .. import benchmarks, plots, report
from ..costs import build_cost_model
from ..walkforward import build_grid, run_fixed_split, run_walk_forward
from ._common import add_common_arguments, is_synthetic, resolve


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--train-years", type=int, default=4)
    parser.add_argument("--test-years", type=int, default=1)
    parser.add_argument("--step-years", type=int, default=1)
    parser.add_argument(
        "--anchored",
        action="store_true",
        help="expanding rather than rolling in-sample window",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=20,
        help="in-sample runs with fewer trades are disqualified",
    )
    parser.add_argument(
        "--fixed-split-end",
        default=None,
        help="also run a single in-sample/out-of-sample split ending on this date",
    )
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args()

    config, universe, panel, manifest = resolve(args)
    os.makedirs(args.out, exist_ok=True)
    costs = build_cost_model(config.costs, universe)

    result = run_walk_forward(
        panel,
        config,
        universe,
        train_years=args.train_years,
        test_years=args.test_years,
        step_years=args.step_years,
        anchored=args.anchored,
        grid=build_grid(),
        min_trades=args.min_trades,
        cost_model=costs,
        progress=lambda line: print(line),
    )

    # The benchmark is measured over the same out-of-sample span, otherwise the
    # comparison would give it the in-sample years for free.
    oos_panel = panel.slice(result.equity.index[0], result.equity.index[-1])
    benchmark_metrics = {}
    curves = {"Walk-forward (out of sample)": result.equity["equity"]}
    for benchmark_symbol in universe.all_benchmarks:
        curve = benchmarks.buy_and_hold(
            oos_panel, benchmark_symbol, config.portfolio.initial_equity, costs
        )
        if curve.dropna().empty:
            continue
        label = f"{benchmark_symbol} buy & hold"
        curves[label] = curve
        benchmark_metrics[label] = benchmarks.curve_metrics(
            curve, config.backtest.risk_free_rate
        )

    figures = []
    if not args.no_figures:
        figures = [
            plots.equity_curve(
                dict(list(curves.items())[:3]),
                os.path.join(args.out, "wf_equity_curve.png"),
                args.theme,
                currency=universe.account_currency,
            ),
            plots.drawdown(
                result.equity["equity"], os.path.join(args.out, "wf_drawdown.png"), args.theme
            ),
            plots.walkforward_windows(
                result.windows, os.path.join(args.out, "wf_windows.png"), args.theme
            ),
        ]

    document = report.build_walkforward_report(
        result,
        config,
        universe.name,
        universe.account_currency,
        benchmark_metrics=benchmark_metrics,
        figures=figures,
        synthetic=is_synthetic(manifest),
    )

    if args.fixed_split_end:
        split = run_fixed_split(
            panel, config, universe, args.fixed_split_end,
            grid=build_grid(), min_trades=args.min_trades, cost_model=costs,
        )
        document += (
            "\n\n## Single fixed split\n\n"
            f"Optimized on data up to {args.fixed_split_end}, then evaluated once "
            "on everything after it. This complements the rolling walk-forward: "
            "one long out-of-sample stretch says something about regime change "
            "rather than about annual re-tuning.\n\n"
            + report.metrics_table(
                {"In sample": split["in_sample"], "Out of sample": split["out_of_sample"]},
                report.HEADLINE_METRICS,
            )
            + f"\n\nChosen parameters: `{split['chosen'] or 'defaults'}`\n"
        )
        with open(os.path.join(args.out, "fixed_split.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {"chosen": split["chosen"], "in_sample": split["in_sample"],
                 "out_of_sample": split["out_of_sample"]},
                handle, indent=2, default=str,
            )

    report.write_report(document, os.path.join(args.out, "report.md"))
    result.windows.to_csv(os.path.join(args.out, "windows.csv"), index=False)
    result.equity.to_csv(os.path.join(args.out, "oos_equity.csv"))
    result.trades.to_csv(os.path.join(args.out, "oos_trades.csv"), index=False)
    with open(os.path.join(args.out, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"out_of_sample": result.metrics, "stability": result.stability,
             "benchmarks": benchmark_metrics, "config_hash": config.hash},
            handle, indent=2, default=str,
        )

    print(
        f"\nstitched out-of-sample: CAGR {result.metrics['cagr']:.2%}   "
        f"max drawdown {result.metrics['max_drawdown']:.2%}   "
        f"Calmar {result.metrics['calmar']:.2f}   trades {result.metrics['n_trades']}"
    )
    print(result.stability.get("verdict", ""))
    print(f"report written to {os.path.join(args.out, 'report.md')}")


if __name__ == "__main__":
    main()
