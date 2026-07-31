"""Signal generation.

This module is pure: it turns one symbol's own bars into a table of booleans and
scores, with no knowledge of the portfolio, the cash balance or the other
symbols. Everything that couples symbols to one another -- position limits,
portfolio heat, cash, ranking against a limited number of slots -- lives in the
engine.

The rules, all evaluated at the close of bar T and acted on at the open of the
symbol's next own session:

Trend
    1. close above the long average
    2. the long average above its own value ``sma_slope_lookback`` bars ago
    3. the mid average above the long average
Pullback
    4. some low in the last ``pullback_touch_window`` bars reached the pullback
       average
    5. the lowest low in that window is not more than
       ``pullback_max_depth_atr`` ATRs below the pullback average, so that a
       collapse does not qualify as a pullback
Confirmation, on bar T itself
    6. close back above the pullback average
    7. close above the previous bar's high
    8. close above its own open
Tradability
    9. price above ``min_price_local`` and 20-day traded value above
       ``min_adv_acct`` once converted to the account currency
   10. enough history for every indicator to be fully warm

Condition 5 is the one that keeps this from being a falling-knife strategy, and
condition 7 is what keeps it from entering while the dip is still in progress.
"""

from typing import Optional

import numpy as np
import pandas as pd

from . import indicators
from .config import StrategyParams

SIGNAL_COLUMNS = [
    "sma_pull",
    "sma_mid",
    "sma_long",
    "atr",
    "adv_value_local",
    "adv_shares",
    "trend_ok",
    "touched",
    "depth_ok",
    "confirm",
    "liquidity_ok",
    "warm",
    "entry_raw",
    "rank_score",
    "below_long",
]


def compute_signals(
    frame: pd.DataFrame,
    params: StrategyParams,
    fx_series: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Evaluate the entry rules on one symbol's native bars.

    Args:
        frame: Canonical OHLCV frame on the symbol's own session index. It must
            not have been reindexed onto a shared calendar: a forward-filled bar
            would deflate the ATR and flatten the moving average slope.
        params: Strategy parameters.
        fx_series: Account currency per unit of the symbol's currency, indexed
            like ``frame``. None means the symbol is already quoted in the
            account currency.

    Returns:
        A frame on the same index carrying the indicator values, the individual
        rule outcomes, the combined ``entry_raw`` flag and the ranking score.
    """
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    open_ = frame["open"]
    volume = frame["volume"]

    sma_pull = indicators.sma(close, params.sma_pullback)
    sma_mid = indicators.sma(close, params.sma_mid)
    sma_long = indicators.sma(close, params.sma_long)
    atr = indicators.wilder_atr(high, low, close, params.atr_period)
    adv_value_local = indicators.average_traded_value(close, volume, 20)
    adv_shares = indicators.average_volume(volume, 20)

    long_rising = indicators.is_rising(sma_long, params.sma_slope_lookback)
    trend_ok = (close > sma_long) & long_rising & (sma_mid > sma_long)

    window = params.pullback_touch_window
    touch_flag = (low <= sma_pull).astype(float)
    touched = touch_flag.rolling(window=window, min_periods=window).max().fillna(0.0) > 0.0
    window_low = low.rolling(window=window, min_periods=window).min()
    depth_ok = window_low >= (sma_pull - params.pullback_max_depth_atr * atr)

    confirm = (close > sma_pull) & (close > high.shift(1)) & (close > open_)

    if fx_series is None:
        rate = pd.Series(1.0, index=frame.index)
    else:
        rate = fx_series.reindex(frame.index).astype(float)
    liquidity_ok = (close >= params.min_price_local) & (
        (adv_value_local * rate) >= params.min_adv_acct
    )

    warm = pd.Series(
        np.arange(len(frame)) >= params.warmup_bars, index=frame.index
    )
    finite = atr.notna() & (atr > 0) & sma_long.notna() & sma_pull.notna()

    entry_raw = (
        trend_ok.fillna(False)
        & touched
        & depth_ok.fillna(False)
        & confirm.fillna(False)
        & liquidity_ok.fillna(False)
        & warm
        & finite
    )

    with np.errstate(divide="ignore", invalid="ignore"):
        rank_score = (close / sma_long - 1.0) / (atr / close)
    rank_score = rank_score.replace([np.inf, -np.inf], np.nan)

    below_long = (close < sma_long).fillna(False)

    return pd.DataFrame(
        {
            "sma_pull": sma_pull,
            "sma_mid": sma_mid,
            "sma_long": sma_long,
            "atr": atr,
            "adv_value_local": adv_value_local,
            "adv_shares": adv_shares,
            "trend_ok": trend_ok.fillna(False),
            "touched": touched,
            "depth_ok": depth_ok.fillna(False),
            "confirm": confirm.fillna(False),
            "liquidity_ok": liquidity_ok.fillna(False),
            "warm": warm,
            "entry_raw": entry_raw,
            "rank_score": rank_score,
            "below_long": below_long,
        },
        index=frame.index,
    )


def compute_regime(frame: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """Evaluate the market regime filter on the regime instrument's own bars.

    Args:
        frame: Canonical OHLCV frame of the regime instrument.
        params: Strategy parameters.

    Returns:
        A boolean series that is True when the regime instrument closes above
        its long average, so that new entries are permitted.
    """
    regime_sma = indicators.sma(frame["close"], params.regime_sma)
    return (frame["close"] > regime_sma).fillna(False)


def initial_stop(fill_price: float, atr_at_signal: float, params: StrategyParams) -> float:
    """The initial stop level, anchored to the price actually paid.

    Args:
        fill_price: Executed entry price in the symbol's own currency.
        atr_at_signal: ATR at the close of the signal bar.
        params: Strategy parameters.

    Returns:
        The stop level in the symbol's own currency.
    """
    return fill_price - params.atr_stop_mult * atr_at_signal


def trailing_stop(
    previous_stop: float, highest_close: float, atr_now: float, params: StrategyParams
) -> float:
    """Advance a Chandelier trailing stop by one bar.

    The stop ratchets upwards only. It is recomputed at the close of each bar
    and is the level that the *next* bar is checked against, which is what keeps
    the exit free of look-ahead.

    Args:
        previous_stop: The stop level in force until now.
        highest_close: Highest close since entry, including the current bar.
        atr_now: ATR at the current close.
        params: Strategy parameters.

    Returns:
        The new stop level, never below ``previous_stop``.
    """
    if not np.isfinite(atr_now):
        return previous_stop
    candidate = highest_close - params.atr_trail_mult * atr_now
    return max(previous_stop, candidate)
