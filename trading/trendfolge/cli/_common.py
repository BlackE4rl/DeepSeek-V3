"""Shared argument parsing and loading for the command line entry points."""

import argparse
import json
import os
from typing import Dict, List, Tuple

from ..config import Config, apply_dotted_overrides, load_config
from ..datasets.panel import Panel, load_panel
from ..universe import Universe, load_universe

DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "configs",
    "default.yaml",
)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the arguments every analysis entry point shares.

    Args:
        parser: The parser to extend.
    """
    parser.add_argument(
        "--data-dir",
        required=True,
        help="directory holding ohlcv/ and fx/ subdirectories of CSV files",
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="universe YAML file; defaults to universe.yaml inside --data-dir",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, help="configuration YAML file"
    )
    parser.add_argument("--start", default=None, help="first date to trade, ISO format")
    parser.add_argument("--end", default=None, help="last date to trade, ISO format")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="SECTION.FIELD=VALUE",
        help="override a single configuration value; may be repeated",
    )
    parser.add_argument("--out", required=True, help="directory to write results into")
    parser.add_argument(
        "--theme",
        default="light",
        choices=("light", "dark"),
        help="colour theme of the generated figures",
    )


def resolve(args: argparse.Namespace) -> Tuple[Config, Universe, Panel, Dict[str, object]]:
    """Load the configuration, the universe, the data manifest and the panel.

    Args:
        args: Parsed command line arguments.

    Returns:
        A tuple of the resolved configuration, the universe, the assembled panel
        and the data manifest.
    """
    universe_path = args.universe or os.path.join(args.data_dir, "universe.yaml")
    universe = load_universe(universe_path)
    config = load_config(args.config)

    # Precedence, lowest to highest: the configuration file, then defaults
    # derived from the universe, then anything the caller asked for explicitly.
    # Applying them the other way round would silently discard a --set.
    known_symbols = {meta.symbol for meta in universe.all_symbols}
    universe_defaults: Dict[str, object] = {}
    if universe.benchmark_symbol:
        universe_defaults["benchmark_symbol"] = universe.benchmark_symbol
    if universe_defaults:
        config = config.with_overrides(backtest=universe_defaults)
    if config.strategy.regime_symbol not in known_symbols and universe.benchmark_symbol:
        # The configured regime instrument is not part of this universe, so it
        # could not be loaded. Fall back to the universe's own benchmark.
        config = config.with_overrides(
            strategy={"regime_symbol": universe.benchmark_symbol}
        )

    config = apply_dotted_overrides(config, _parse_overrides(args.set))

    backtest_overrides: Dict[str, object] = {}
    if args.start is not None:
        backtest_overrides["start"] = args.start
    if args.end is not None:
        backtest_overrides["end"] = args.end
    if backtest_overrides:
        config = config.with_overrides(backtest=backtest_overrides)

    manifest = load_manifest(args.data_dir)
    panel = load_panel(args.data_dir, universe, config)
    return config, universe, panel, manifest


def load_manifest(data_dir: str) -> Dict[str, object]:
    """Read the data manifest written by the download or generation step.

    Args:
        data_dir: The data directory.

    Returns:
        The manifest, or an empty dictionary when none exists.
    """
    path = os.path.join(data_dir, "_manifest.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_synthetic(manifest: Dict[str, object]) -> bool:
    """Whether the data was generated rather than observed in a market."""
    return str(manifest.get("source", "")).lower() == "synthetic"


def write_manifest(data_dir: str, manifest: Dict[str, object]) -> str:
    """Write a data manifest next to the price files."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "_manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True, default=str)
    return path


def manifest_notes(manifest: Dict[str, object]) -> List[str]:
    """Human readable provenance lines for the report header."""
    if not manifest:
        return ["Data provenance: unknown (no _manifest.json in the data directory)"]
    notes = [f"Data source: {manifest.get('source', 'unknown')}"]
    if "downloaded_at" in manifest:
        notes.append(f"Downloaded: {manifest['downloaded_at']}")
    if "seed" in manifest:
        notes.append(f"Generator seed: {manifest['seed']}")
    if "symbols" in manifest:
        notes.append(f"Symbols in cache: {len(manifest['symbols'])}")
    return notes


def _parse_overrides(pairs: List[str]) -> Dict[str, str]:
    """Turn ``section.field=value`` strings into a mapping."""
    overrides: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects SECTION.FIELD=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        overrides[key.strip()] = value.strip()
    return overrides
