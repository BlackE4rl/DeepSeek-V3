"""Look-ahead tests.

These are the most important tests in the suite. A backtest that peeks at the
future produces excellent results and no warning of any kind, so the property is
asserted directly rather than inferred from plausible-looking output:

* the prefix test truncates the data at a date and requires the trade list up to
  that date to be byte-identical to the full run's;
* the mutation test rewrites every bar after a date with different numbers and
  requires the trades before it to be unchanged;
* the fill tests pin down that an entry executes at the next session's open and
  never at the signal bar's close, and that a stop fills against the level fixed
  at the previous close.
"""

import numpy as np
import pandas as pd
import pytest

from trendfolge.datasets.fx import load_fx_rates
from trendfolge.datasets.loader import load_universe_prices
from trendfolge.datasets.normalize import PriceData
from trendfolge.datasets.panel import build_panel
from trendfolge.engine import run_backtest

CUT = "2018-06-29"
COMPARED_COLUMNS = [
    "symbol",
    "entry_date",
    "exit_date",
    "shares",
    "entry_price_local",
    "exit_price_local",
    "net_pnl_acct",
    "exit_reason",
]


def _build_panel(synth_dir, universe, config, end=None, mutate=None):
    """Load a panel, optionally truncating or rewriting the data first."""
    symbols = [meta.symbol for meta in universe.all_symbols]
    price_data = load_universe_prices(
        synth_dir, symbols, price_mode=config.data.price_mode, end=end
    )
    if mutate is not None:
        price_data = mutate(price_data)
    fx = load_fx_rates(synth_dir, universe.fx_pairs, universe.account_currency)
    return build_panel(price_data, universe, config, fx)


def _rewrite_future(price_data, cut: str, seed: int = 99):
    """Scale every bar after ``cut`` by a random walk, preserving bar geometry."""
    rng = np.random.default_rng(seed)
    rewritten = {}
    for symbol, data in price_data.items():
        frame = data.frame.copy()
        after = frame.index > pd.Timestamp(cut)
        count = int(after.sum())
        if count:
            factors = np.cumprod(1.0 + rng.normal(0.0, 0.02, count))
            for column in ("open", "high", "low", "close", "adj_close"):
                frame.loc[after, column] = frame.loc[after, column].to_numpy() * factors
        rewritten[symbol] = PriceData(
            symbol=symbol,
            frame=frame,
            is_adjusted=data.is_adjusted,
            source=data.source,
            path=data.path,
        )
    return rewritten


@pytest.fixture(scope="module")
def _cache():
    return {}


def _full_panel(synth_dir, universe, config, cache):
    if "panel" not in cache:
        cache["panel"] = _build_panel(synth_dir, universe, config)
    return cache["panel"]


def _full_result(synth_dir, universe, config, cache):
    if "full" not in cache:
        cache["full"] = run_backtest(
            _full_panel(synth_dir, universe, config, cache), config, universe
        )
    return cache["full"]


def test_the_synthetic_run_actually_trades(synth_dir, synth_universe, synth_config, _cache):
    """A causality test over an empty trade list would prove nothing."""
    result = _full_result(synth_dir, synth_universe, synth_config, _cache)

    assert len(result.trades) > 50
    assert result.trades["exit_reason"].nunique() >= 2


def test_truncating_the_data_does_not_change_earlier_trades(
    synth_dir, synth_universe, synth_config, _cache
):
    """The prefix property: the past may not depend on data that came later."""
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    truncated = run_backtest(
        _build_panel(synth_dir, synth_universe, synth_config, end=CUT),
        synth_config,
        synth_universe,
    )

    cut = pd.Timestamp(CUT)
    expected = full.trades[full.trades["exit_date"] <= cut].reset_index(drop=True)
    actual = truncated.trades[truncated.trades["exit_date"] <= cut].reset_index(drop=True)

    assert len(expected) > 20, "the comparison window must contain real trades"
    pd.testing.assert_frame_equal(
        expected[COMPARED_COLUMNS], actual[COMPARED_COLUMNS]
    )


def test_rewriting_the_future_does_not_change_earlier_trades(
    synth_dir, synth_universe, synth_config, _cache
):
    """The mutation property: unknowable data may not influence past decisions."""
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    mutated = run_backtest(
        _build_panel(
            synth_dir,
            synth_universe,
            synth_config,
            mutate=lambda data: _rewrite_future(data, CUT),
        ),
        synth_config,
        synth_universe,
    )

    cut = pd.Timestamp(CUT)
    expected = full.trades[full.trades["exit_date"] <= cut].reset_index(drop=True)
    actual = mutated.trades[mutated.trades["exit_date"] <= cut].reset_index(drop=True)

    pd.testing.assert_frame_equal(
        expected[COMPARED_COLUMNS], actual[COMPARED_COLUMNS]
    )


def test_equity_before_the_cut_is_identical_when_the_future_is_rewritten(
    synth_dir, synth_universe, synth_config, _cache
):
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    mutated = run_backtest(
        _build_panel(
            synth_dir,
            synth_universe,
            synth_config,
            mutate=lambda data: _rewrite_future(data, CUT, seed=7),
        ),
        synth_config,
        synth_universe,
    )

    cut = pd.Timestamp(CUT)
    left = full.equity.loc[:cut, "equity"]
    right = mutated.equity.loc[:cut, "equity"]

    pd.testing.assert_series_equal(left, right)


def test_entries_fill_at_the_next_session_open_never_at_the_signal_close(
    synth_dir, synth_universe, synth_config, _cache
):
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    panel = _full_panel(synth_dir, synth_universe, synth_config, _cache)

    adverse = 1.0 + (synth_config.costs.slippage_bps + 3.0) * 1e-4
    for _, trade in full.trades.iterrows():
        symbol = trade["symbol"]
        entry_date = trade["entry_date"]

        assert panel.sessions.loc[entry_date, symbol], "filled on a non-session date"
        implied_reference = trade["entry_price_local"] / adverse
        assert implied_reference == pytest.approx(
            panel.px_open.loc[entry_date, symbol], rel=1e-9
        )


def test_stop_exits_never_fill_above_the_open_and_never_below_the_low(
    synth_dir, synth_universe, synth_config, _cache
):
    """The gap rule: a stop that gaps through fills at the open, not at the level."""
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    panel = _full_panel(synth_dir, synth_universe, synth_config, _cache)

    stops = full.trades[full.trades["exit_reason"] == "stop"]
    assert len(stops) > 10

    adverse = 1.0 - (synth_config.costs.slippage_bps + 3.0 + 10.0) * 1e-4
    for _, trade in stops.iterrows():
        symbol = trade["symbol"]
        exit_date = trade["exit_date"]
        reference = trade["exit_price_local"] / adverse

        assert reference <= panel.px_open.loc[exit_date, symbol] * (1 + 1e-9)
        assert reference >= panel.px_low.loc[exit_date, symbol] * (1 - 1e-9)


def test_exits_happen_on_a_session_of_the_symbol(
    synth_dir, synth_universe, synth_config, _cache
):
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    panel = _full_panel(synth_dir, synth_universe, synth_config, _cache)

    for _, trade in full.trades.iterrows():
        assert panel.sessions.loc[trade["exit_date"], trade["symbol"]]
        assert trade["exit_date"] >= trade["entry_date"]


def test_running_twice_gives_identical_results(
    synth_dir, synth_universe, synth_config, _cache
):
    """No hidden global state, no dependence on dictionary iteration luck."""
    panel = _full_panel(synth_dir, synth_universe, synth_config, _cache)
    first = run_backtest(panel, synth_config, synth_universe)
    second = run_backtest(panel, synth_config, synth_universe)

    pd.testing.assert_frame_equal(first.trades, second.trades)
    pd.testing.assert_frame_equal(first.equity, second.equity)


def test_no_trade_starts_before_the_indicator_warmup_is_complete(
    synth_dir, synth_universe, synth_config, _cache
):
    full = _full_result(synth_dir, synth_universe, synth_config, _cache)
    panel = _full_panel(synth_dir, synth_universe, synth_config, _cache)
    warmup = synth_config.strategy.warmup_bars

    earliest = full.trades["entry_date"].min()
    first_possible = panel.master_index[warmup]
    assert earliest > first_possible
