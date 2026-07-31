"""Daily-bar trend-following backtester for technology stocks.

The strategy trades pullbacks to the 20-day moving average inside an
established long-term uptrend, sized by ATR risk and exited by an ATR
trailing stop. See README.md for the full rule set and, more importantly,
for the caveats that determine how much any backtest result is worth.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
