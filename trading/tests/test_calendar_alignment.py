"""Tests for multi-exchange calendar handling.

The rule the whole panel design exists to enforce: execution prices are never
forward filled, valuation prices always are, and an order queued for a symbol
executes at that symbol's next own session rather than at the next calendar
date.
"""

import numpy as np
import pandas as pd
import pytest

from helpers import default_test_config, flat_frame, make_panel, make_universe, ramp_frame
from trendfolge import indicators
from trendfolge.datasets import synthetic
from trendfolge.universe import SymbolMeta


def _two_exchange_panel(periods: int = 400):
    """A panel with a US symbol, a German symbol and a regime stub."""
    start, end = "2015-01-01", "2017-12-31"
    us_sessions = synthetic.make_sessions(start, end, "XNYS")[:periods]
    de_sessions = synthetic.make_sessions(start, end, "XETR")[:periods]

    frames = {
        "US": ramp_frame(us_sessions, start_price=100.0),
        "DE": ramp_frame(de_sessions, start_price=50.0),
        "REGIME": flat_frame(
            synthetic.make_sessions(start, end, "XNAS")[:periods], price=100.0
        ),
    }
    universe = make_universe(
        [
            SymbolMeta("US", "EUR", "XNYS", "US stock"),
            SymbolMeta("DE", "EUR", "XETR", "German stock"),
        ]
    )
    return make_panel(frames, universe, default_test_config())


def test_execution_prices_are_nan_on_non_session_dates():
    panel = _two_exchange_panel()

    for symbol in ("US", "DE"):
        closed = ~panel.sessions[symbol]
        assert closed.any(), "the two calendars are supposed to differ"
        assert panel.px_open.loc[closed, symbol].isna().all()
        assert panel.px_high.loc[closed, symbol].isna().all()
        assert panel.px_low.loc[closed, symbol].isna().all()
        assert panel.px_close.loc[closed, symbol].isna().all()


def test_valuation_prices_are_forward_filled():
    panel = _two_exchange_panel()

    closed = ~panel.sessions["DE"]
    # Everything after the symbol's first session must carry a mark price.
    first = panel.sessions["DE"].idxmax()
    later = panel.master_index > first
    assert panel.px_mark.loc[later, "DE"].notna().all()
    assert panel.px_mark.loc[closed & later, "DE"].notna().all()


def test_mark_price_on_a_closed_day_equals_the_previous_session_close():
    panel = _two_exchange_panel()

    closed_positions = np.flatnonzero(~panel.sessions["DE"].to_numpy())
    closed_positions = [p for p in closed_positions if p > 0]
    assert closed_positions, "expected at least one German holiday"

    position = closed_positions[0]
    previous = panel.px_close["DE"].iloc[:position].last_valid_index()
    assert panel.px_mark["DE"].iloc[position] == pytest.approx(
        panel.px_close["DE"].loc[previous]
    )


def test_next_session_skips_the_holiday():
    panel = _two_exchange_panel()

    closed_positions = np.flatnonzero(~panel.sessions["DE"].to_numpy())
    closed_positions = [p for p in closed_positions if 0 < p < len(panel.master_index) - 5]
    position = closed_positions[0] - 1

    following = panel.next_session_position("DE", position)
    assert following > position
    assert panel.has_session("DE", following)
    # Every date strictly between the two is a non-session for this symbol.
    for between in range(position + 1, following):
        assert not panel.has_session("DE", between)


def test_next_session_returns_minus_one_at_the_end_of_the_range():
    panel = _two_exchange_panel()
    last = len(panel.master_index) - 1

    assert panel.next_session_position("US", last) == -1


def test_indicators_are_computed_on_native_bars_not_on_the_union_index():
    """The ATR of a symbol must not change because another exchange was open."""
    panel = _two_exchange_panel()

    start, end = "2015-01-01", "2017-12-31"
    de_sessions = synthetic.make_sessions(start, end, "XETR")[:400]
    native = ramp_frame(de_sessions, start_price=50.0)
    native_atr = indicators.wilder_atr(
        native["high"], native["low"], native["close"], 14
    )

    panel_atr = panel.signals["DE"]["atr"].reindex(de_sessions)
    pd.testing.assert_series_equal(
        panel_atr.dropna(), native_atr.dropna(), check_names=False
    )


def test_signal_columns_are_nan_on_non_session_dates():
    panel = _two_exchange_panel()

    closed = ~panel.sessions["DE"]
    assert panel.signals["DE"].loc[closed, "atr"].isna().all()
    assert panel.signals["DE"]["entry_raw"].dtype == bool
    assert not panel.signals["DE"].loc[closed, "entry_raw"].any()


def test_close_only_forward_fill_would_deflate_the_atr():
    """Demonstrates one of the biases the panel design exists to avoid.

    This is not a test of production code. It documents, in executable form, the
    consequence of the common shortcut of forward-filling only the close and
    synthesising a flat bar around it: the invented bars have zero range, the
    ATR shrinks, stops tighten and position sizes inflate.
    """
    start, end = "2015-01-01", "2016-12-31"
    de_sessions = synthetic.make_sessions(start, end, "XETR")
    union = synthetic.weekdays(start, end)

    native = ramp_frame(de_sessions, start_price=50.0)
    filled_close = native["close"].reindex(union).ffill().dropna()
    flat = pd.DataFrame(
        {"high": filled_close, "low": filled_close, "close": filled_close}
    )

    native_atr = indicators.wilder_atr(
        native["high"], native["low"], native["close"], 14
    ).iloc[-1]
    flat_atr = indicators.wilder_atr(
        flat["high"], flat["low"], flat["close"], 14
    ).iloc[-1]

    assert flat_atr < native_atr


def test_whole_bar_forward_fill_would_shorten_the_lookback_window():
    """The other bias: an N-bar average would span fewer than N real sessions.

    Forward-filling the whole row duplicates the previous bar rather than
    flattening it, so the ATR survives roughly intact -- but the window no longer
    covers the amount of trading history it claims to, and it covers a different
    amount for every exchange.
    """
    start, end = "2015-01-01", "2016-12-31"
    de_sessions = synthetic.make_sessions(start, end, "XETR")
    union = synthetic.weekdays(start, end)

    window = 200
    last_union_position = len(union) - 1
    covered = union[last_union_position - window + 1 : last_union_position + 1]
    real_sessions = covered.intersection(de_sessions)

    assert len(real_sessions) < window
    # And the shortfall differs by exchange, so the same parameter would mean
    # different things for a German and an American symbol.
    us_sessions = synthetic.make_sessions(start, end, "XNYS")
    assert len(covered.intersection(us_sessions)) != len(real_sessions)


def test_regime_uses_the_last_known_state_on_a_date_it_has_no_session():
    panel = _two_exchange_panel()

    assert panel.regime.notna().all()
    assert panel.regime.dtype == bool


def test_panel_slice_keeps_the_alignment_invariants():
    panel = _two_exchange_panel()
    cut = panel.master_index[len(panel.master_index) // 2]
    sliced = panel.slice(cut, None)

    assert sliced.master_index[0] == cut
    closed = ~sliced.sessions["DE"]
    assert sliced.px_open.loc[closed, "DE"].isna().all()
    assert sliced.next_session_position("DE", len(sliced.master_index) - 1) == -1
