"""Run the robustness analysis: sweeps, heatmap, cost stress and bootstrap."""

import argparse
import json
import os

from .. import plots, report, sensitivity
from ..costs import build_cost_model
from ..engine import run_backtest
from ._common import add_common_arguments, is_synthetic, resolve


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument(
        "--bootstrap-samples", type=int, default=1000, help="bootstrap replications"
    )
    parser.add_argument("--seed", type=int, default=12345, help="bootstrap RNG seed")
    parser.add_argument(
        "--skip-sweeps",
        action="store_true",
        help="only run the bootstrap and the cost stress, which are much faster",
    )
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args()

    config, universe, panel, manifest = resolve(args)
    os.makedirs(args.out, exist_ok=True)
    costs = build_cost_model(config.costs, universe)

    sweeps = (
        sensitivity.sweep_all(panel, config, universe, progress=lambda line: print(line))
        if not args.skip_sweeps
        else sensitivity.sweep_all(panel, config, universe, strategy_axes={}, portfolio_axes={})
    )
    verdict = sensitivity.robustness_verdict(sweeps)

    heatmap_matrix = None
    if not args.skip_sweeps:
        print("sweeping atr_stop_mult against atr_trail_mult")
        heatmap_matrix = sensitivity.sweep_two(
            panel, config, universe,
            "atr_stop_mult", (2.0, 2.5, 3.0, 3.5),
            "atr_trail_mult", (2.5, 3.0, 3.5, 4.0, 5.0),
        )

    print("running the cost stress test")
    stress = sensitivity.cost_stress(panel, config, universe)

    baseline = run_backtest(panel, config, universe, costs)
    bootstrap = sensitivity.bootstrap_trades(
        baseline.trades,
        config.portfolio.initial_equity,
        n_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    summary = sensitivity.bootstrap_summary(bootstrap)

    figures = []
    if not args.no_figures:
        if not sweeps.empty:
            for parameter in sweeps["parameter"].unique():
                figures.append(
                    plots.sensitivity_panels(
                        sweeps, parameter,
                        os.path.join(args.out, f"sens_{parameter}.png"), args.theme,
                    )
                )
        if heatmap_matrix is not None:
            figures.append(
                plots.heatmap(
                    heatmap_matrix,
                    os.path.join(args.out, "heatmap_stop_trail.png"),
                    args.theme,
                )
            )
        figures.append(
            plots.bootstrap_distribution(
                bootstrap, os.path.join(args.out, "bootstrap_terminal.png"), args.theme
            )
        )

    document = report.build_sensitivity_report(
        sweeps,
        verdict,
        stress,
        summary,
        heatmap_matrix,
        config,
        universe.name,
        universe.account_currency,
        figures=figures,
        synthetic=is_synthetic(manifest),
    )
    report.write_report(document, os.path.join(args.out, "report.md"))

    sweeps.to_csv(os.path.join(args.out, "sweeps.csv"), index=False)
    stress.to_csv(os.path.join(args.out, "cost_stress.csv"), index=False)
    if heatmap_matrix is not None:
        heatmap_matrix.to_csv(os.path.join(args.out, "heatmap_stop_trail.csv"))
    if not bootstrap.empty:
        bootstrap.to_csv(os.path.join(args.out, "bootstrap.csv"), index=False)
    with open(os.path.join(args.out, "verdict.json"), "w", encoding="utf-8") as handle:
        json.dump({"verdict": verdict, "bootstrap": summary, "config_hash": config.hash},
                  handle, indent=2, default=str)

    print("\n" + str(verdict["verdict"]))
    if summary:
        print(
            f"bootstrap total return: p5 {summary['total_return_p5']:.1%}  "
            f"p50 {summary['total_return_p50']:.1%}  "
            f"p95 {summary['total_return_p95']:.1%}"
        )
    print(f"report written to {os.path.join(args.out, 'report.md')}")


if __name__ == "__main__":
    main()
