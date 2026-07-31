"""Shared fixtures.

The synthetic universe is generated once per test session because building
twenty years of bars for seventeen symbols is the most expensive thing the suite
does. It is written to a temporary directory and never leaves it.
"""

import os

import pytest

from trendfolge.config import Config, load_config
from trendfolge.datasets import synthetic
from trendfolge.universe import load_universe

SYNTHETIC_START = "2005-01-03"
SYNTHETIC_END = "2024-12-31"


@pytest.fixture(scope="session")
def synth_dir(tmp_path_factory) -> str:
    """Directory holding a generated synthetic universe with its universe file."""
    directory = str(tmp_path_factory.mktemp("synthetic"))
    synthetic.make_universe(
        directory, seed=42, start=SYNTHETIC_START, end=SYNTHETIC_END
    )
    with open(os.path.join(directory, "universe.yaml"), "w", encoding="utf-8") as handle:
        handle.write(synthetic.synthetic_universe_yaml())
    return directory


@pytest.fixture(scope="session")
def synth_universe(synth_dir):
    """The generated universe definition."""
    return load_universe(os.path.join(synth_dir, "universe.yaml"))


@pytest.fixture()
def synth_config(synth_universe) -> Config:
    """A configuration pointing the regime filter at the synthetic benchmark."""
    return Config().with_overrides(
        strategy={"regime_symbol": synth_universe.benchmark_symbol},
        backtest={"benchmark_symbol": synth_universe.benchmark_symbol},
    )


@pytest.fixture(scope="session")
def default_config_file() -> str:
    """Path to the shipped default configuration."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "default.yaml",
    )


@pytest.fixture()
def shipped_config(default_config_file) -> Config:
    """The shipped default configuration, as loaded from disk."""
    return load_config(default_config_file)
