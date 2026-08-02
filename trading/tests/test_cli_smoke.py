"""End-to-end smoke tests of the command line entry points.

These drive the whole chain -- generate data, backtest, walk-forward, robustness
-- into a temporary directory and check that the expected artifacts appear and
that every reported number is finite. They use deliberately small date ranges and
grids so the suite stays fast; the point is that the wiring holds, not that the
numbers are interesting.
"""

import json
import os
import runpy
import sys

import numpy as np
import pandas as pd
import pytest

from trendfolge.cli import _common


def _run_module(module: str, argv):
    """Execute a CLI module as if it had been launched with ``python -m``."""
    original = sys.argv
    sys.argv = [module] + list(argv)
    try:
        runpy.run_module(module, run_name="__main__")
    finally:
        sys.argv = original


@pytest.fixture(scope="module")
def cli_data(tmp_path_factory):
    """A small generated market written by the make_synthetic entry point."""
    directory = str(tmp_path_factory.mktemp("cli") / "data")
    _run_module(
        "trendfolge.cli.make_synthetic",
        ["--out", directory, "--seed", "5", "--start", "2005-01-03", "--end", "2016-12-31"],
    )
    return directory


def test_make_synthetic_writes_data_universe_and_manifest(cli_data):
    assert os.path.isdir(os.path.join(cli_data, "ohlcv"))
    assert os.path.isdir(os.path.join(cli_data, "fx"))
    assert os.path.exists(os.path.join(cli_data, "universe.yaml"))

    manifest = _common.load_manifest(cli_data)
    assert _common.is_synthetic(manifest)
    assert manifest["seed"] == 5
    assert len(manifest["symbols"]) > 10


def test_backtest_cli_writes_every_expected_artifact(cli_data, tmp_path):
    out = str(tmp_path / "backtest")
    _run_module("trendfolge.cli.run_backtest", ["--data-dir", cli_data, "--out", out])

    for name in (
        "report.md", "metrics.json", "trades.csv", "equity.csv",
        "rejected_orders.csv", "equity_curve.png", "drawdown.png",
        "annual_returns.png", "exposure.png", "trade_r_hist.png",
    ):
        path = os.path.join(out, name)
        assert os.path.exists(path), f"{name} was not written"
        assert os.path.getsize(path) > 0


def test_backtest_metrics_are_all_finite(cli_data, tmp_path):
    out = str(tmp_path / "backtest_metrics")
    _run_module("trendfolge.cli.run_backtest", ["--data-dir", cli_data, "--out", out,
                                                "--no-figures"])

    with open(os.path.join(out, "metrics.json"), "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    for section in ("strategy_net", "strategy_gross"):
        metrics = payload[section]
        assert metrics["n_trades"] > 0
        for key in ("cagr", "max_drawdown", "sharpe", "final_equity"):
            assert np.isfinite(metrics[key]), f"{section}.{key} is not finite"


def test_the_report_always_carries_the_caveats_and_the_config_hash(cli_data, tmp_path):
    out = str(tmp_path / "report_content")
    _run_module("trendfolge.cli.run_backtest", ["--data-dir", cli_data, "--out", out,
                                                "--no-figures"])

    with open(os.path.join(out, "report.md"), "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "## Caveats" in text
    assert "not financial advice" in text
    assert "Configuration hash" in text
    # Synthetic data must be flagged loudly rather than quietly.
    assert "synthetic data" in text.lower()
    # The buy-and-hold comparison has to be the headline, not an appendix.
    assert text.index("Headline") < text.index("Trade statistics")


def test_the_report_carries_the_after_tax_comparison(cli_data, tmp_path):
    """The tax section is what makes the ETF comparison meaningful."""
    out = str(tmp_path / "after_tax")
    _run_module("trendfolge.cli.run_backtest", ["--data-dir", cli_data, "--out", out,
                                                "--no-figures"])

    with open(os.path.join(out, "report.md"), "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "## After tax" in text
    assert "Teilfreistellung" in text
    assert "Basiszins" in text
    # Both fund treatments must be shown, so neither is a hidden assumption.
    assert "after tax (sold)" in text
    assert "after tax (held)" in text
    # DEGIRO: assessed, not withheld.
    assert "nothing is withheld" in text

    with open(os.path.join(out, "metrics.json"), "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert payload["after_tax"], "no after-tax metrics were produced"
    assert "Strategy after tax" in payload["after_tax"]
    strategy = payload["tax_summaries"]["Strategy"]
    assert strategy["total_tax"] > 0
    assert np.isfinite(strategy["tax_share_of_gain"])


def test_tax_reduces_the_strategy_and_can_be_switched_off(cli_data, tmp_path):
    taxed = str(tmp_path / "taxed")
    untaxed = str(tmp_path / "untaxed")
    _run_module("trendfolge.cli.run_backtest",
                ["--data-dir", cli_data, "--out", taxed, "--no-figures"])
    _run_module("trendfolge.cli.run_backtest",
                ["--data-dir", cli_data, "--out", untaxed, "--no-figures",
                 "--set", "tax.enabled=false"])

    def final(path, key):
        with open(os.path.join(path, "metrics.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)[key]

    after = final(taxed, "after_tax")["Strategy after tax"]["final_equity"]
    before = final(taxed, "strategy_net")["final_equity"]
    assert after < before

    with open(os.path.join(untaxed, "report.md"), "r", encoding="utf-8") as handle:
        assert "Tax modelling is switched off" in handle.read()


def test_an_exposure_matched_benchmark_is_reported(cli_data, tmp_path):
    """Comparing a mostly-flat strategy to a fully invested ETF is not a comparison."""
    out = str(tmp_path / "exposure")
    _run_module("trendfolge.cli.run_backtest", ["--data-dir", cli_data, "--out", out,
                                                "--no-figures"])

    with open(os.path.join(out, "metrics.json"), "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    matched = [name for name in payload["benchmarks"] if "exposure" in name]
    assert matched, "expected an exposure-matched benchmark column"
    plain = [
        name for name in payload["benchmarks"]
        if "buy & hold" in name and "exposure" not in name
    ]
    # Holding a fraction of the benchmark must be less volatile than all of it.
    assert (
        payload["benchmarks"][matched[0]]["annual_volatility"]
        < payload["benchmarks"][plain[0]]["annual_volatility"]
    )


def test_configuration_overrides_reach_the_report(cli_data, tmp_path):
    out = str(tmp_path / "override")
    _run_module(
        "trendfolge.cli.run_backtest",
        ["--data-dir", cli_data, "--out", out, "--no-figures",
         "--set", "strategy.atr_stop_mult=3.5", "--set", "portfolio.max_positions=3"],
    )

    with open(os.path.join(out, "report.md"), "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "an initial stop 3.5 ATRs below the fill" in text
    assert "at most 3 positions" in text


def test_an_explicit_override_beats_the_universe_derived_default(cli_data, tmp_path):
    """--set must win over the defaults the universe file supplies."""
    out = str(tmp_path / "precedence")
    _run_module(
        "trendfolge.cli.run_backtest",
        ["--data-dir", cli_data, "--out", out, "--no-figures",
         "--set", "strategy.regime_sma=150"],
    )

    with open(os.path.join(out, "report.md"), "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "above its 150-day average" in text


def test_walkforward_cli_produces_a_stitched_curve(cli_data, tmp_path):
    out = str(tmp_path / "walkforward")
    _run_module(
        "trendfolge.cli.run_walkforward",
        ["--data-dir", cli_data, "--out", out, "--train-years", "3",
         "--test-years", "1", "--min-trades", "1", "--no-figures"],
    )

    for name in ("report.md", "windows.csv", "oos_equity.csv", "oos_trades.csv",
                 "metrics.json"):
        assert os.path.exists(os.path.join(out, name)), f"{name} was not written"

    windows = pd.read_csv(os.path.join(out, "windows.csv"))
    assert len(windows) >= 3
    assert (pd.to_datetime(windows["test_start"]) > pd.to_datetime(windows["train_end"])).all()

    equity = pd.read_csv(os.path.join(out, "oos_equity.csv"), index_col=0, parse_dates=True)
    assert equity.index.is_monotonic_increasing
    assert np.isfinite(equity["equity"]).all()


def test_walkforward_report_refuses_to_headline_the_in_sample_numbers(cli_data, tmp_path):
    out = str(tmp_path / "walkforward_text")
    _run_module(
        "trendfolge.cli.run_walkforward",
        ["--data-dir", cli_data, "--out", out, "--train-years", "3",
         "--test-years", "1", "--min-trades", "1", "--no-figures"],
    )

    with open(os.path.join(out, "report.md"), "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "stitched out-of-sample curve below is **the** result" in text
    assert "Parameter stability" in text
    assert "## Caveats" in text


def test_sensitivity_cli_writes_a_verdict(cli_data, tmp_path):
    out = str(tmp_path / "sensitivity")
    _run_module(
        "trendfolge.cli.run_sensitivity",
        ["--data-dir", cli_data, "--out", out, "--skip-sweeps",
         "--bootstrap-samples", "100", "--no-figures"],
    )

    for name in ("report.md", "cost_stress.csv", "verdict.json"):
        assert os.path.exists(os.path.join(out, name)), f"{name} was not written"

    with open(os.path.join(out, "verdict.json"), "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert "verdict" in payload["verdict"]
    assert "70%" in payload["verdict"]["rule"]

    stress = pd.read_csv(os.path.join(out, "cost_stress.csv"))
    assert list(stress["cost_factor"]) == [0.0, 1.0, 2.0, 4.0]
    assert stress["final_equity"].iloc[0] >= stress["final_equity"].iloc[-1]


def test_an_unknown_override_key_is_rejected_rather_than_ignored(cli_data, tmp_path):
    """A silently ignored typo runs the backtest with settings you did not choose."""
    out = str(tmp_path / "bad_override")

    with pytest.raises(Exception, match="unknown field"):
        _run_module(
            "trendfolge.cli.run_backtest",
            ["--data-dir", cli_data, "--out", out, "--no-figures",
             "--set", "strategy.atr_stop_multiplier=3.0"],
        )


def test_malformed_override_syntax_is_rejected(cli_data, tmp_path):
    out = str(tmp_path / "bad_syntax")

    with pytest.raises(SystemExit):
        _run_module(
            "trendfolge.cli.run_backtest",
            ["--data-dir", cli_data, "--out", out, "--no-figures", "--set", "nonsense"],
        )
