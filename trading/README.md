# trendfolge — a daily-bar trend-following backtester for technology stocks

A rule-based long-only strategy that buys pullbacks to the 20-day moving average
inside an established long-term uptrend, sizes positions by ATR risk, and exits
on an ATR trailing stop — together with the machinery needed to find out whether
it is worth anything: walk-forward validation, realistic transaction costs,
benchmark comparison, parameter sweeps and a bootstrap.

This directory is self-contained and has nothing to do with the DeepSeek-V3
inference code in the rest of the repository.

---

## Read this first

**No real market data was used to produce anything in this repository.** The
session this code was written in blocks outbound connections to the market data
vendors, so every run here used *generated* prices. Those runs prove that the
code executes and that the accounting balances. They say nothing whatsoever
about whether the strategy makes money.

To get a real answer you have to fetch real prices on your own machine and run
the walk-forward yourself. That takes about five minutes; see below.

**And a strategy cannot be promised to be successful in advance.** What is
delivered here is an honestly implemented rule set plus an evaluation that is
strict enough to tell you if it *isn't* working — which is the more useful half.
The section at the end lists, in detail, the reasons a good-looking result here
would still not justify trading real money.

---

## Setup

```bash
cd trading
pip install -r requirements.txt
```

Python 3.10 or newer. The core requirements contain no networking library at
all, on purpose: the backtester must be runnable and testable offline.

Run the test suite to confirm everything works:

```bash
python -m pytest
```

## Getting real data

```bash
pip install -r requirements-download.txt

python -m trendfolge.cli.download_data \
    --universe configs/universe_megacap.yaml \
    --start 2005-01-01 \
    --out data
```

This writes one CSV per symbol into `data/ohlcv/`, the exchange rate series into
`data/fx/`, a copy of the universe file, and a `_manifest.json` recording when
the data was fetched and the SHA-256 of every file, so any report can name the
bytes it was computed from.

If you already have daily bars from elsewhere, you can skip the download: the
loader reads **Yahoo**, **Stooq** and **TradingView** CSV exports and detects
which is which from the header. Drop them in `data/ohlcv/` named `<TICKER>.csv`.

## Running it

```bash
# a single backtest with a full report
python -m trendfolge.cli.run_backtest --data-dir data --out reports/run1

# the walk-forward — this is the number that counts
python -m trendfolge.cli.run_walkforward --data-dir data --out reports/wf \
    --fixed-split-end 2018-12-31

# robustness: sweeps, heatmap, cost stress, bootstrap
python -m trendfolge.cli.run_sensitivity --data-dir data --out reports/sens
```

Each writes `report.md` plus the CSVs, JSON and PNGs behind it. Add
`--theme dark` for dark-mode figures, `--no-figures` to skip them.

Every entry point accepts `--set section.field=value` to override a single
parameter, repeatable:

```bash
python -m trendfolge.cli.run_backtest --data-dir data --out reports/tight \
    --set strategy.atr_stop_mult=2.0 --set portfolio.max_positions=4
```

A misspelled key is a hard error rather than a silent no-op — otherwise you end
up running a backtest with settings you believe you changed and did not.

### Without real data

The whole pipeline runs offline on generated prices:

```bash
python -m trendfolge.cli.make_synthetic --out data/synthetic --seed 42
python -m trendfolge.cli.run_backtest --data-dir data/synthetic --out reports/demo
```

Reports produced from generated data carry a warning banner saying so.

---

## The rules

Long only, no leverage, no shorting. **Every signal is evaluated at the close of
day T and executed at the open of the symbol's next own trading session.** That
one sentence is the entire look-ahead defence, and two tests enforce it.

### Entry — all conditions on bar T

**Market regime** (portfolio-level gate on new entries only):

- QQQ closes above its 200-day average.

**Trend** (per symbol):

1. close above the 200-day average
2. the 200-day average above its own value 20 bars ago — the trend must be
   *rising*, not merely above
3. the 50-day average above the 200-day average

**Pullback** — the actual setup:

4. some low within the last 5 bars reached the 20-day average
5. the lowest low in that window is no more than 1.5 ATRs below the average

Condition 5 is what keeps this from being a falling-knife strategy: a drop to
three ATRs below the average is not a pullback, it is a change of trend.

**Confirmation** — on bar T itself:

6. close back above the 20-day average
7. close above the previous bar's high
8. close above its own open

Conditions 7 and 8 are what stop it entering while the dip is still in progress.

**Tradability**: price above 5 in local currency, 20-day traded value above
20 million in account currency, symbol not already held, not within 5 bars of
its own last exit, a free slot and risk headroom available.

When more symbols qualify than there are free slots, they are ranked by

```
(close / SMA200 − 1) / (ATR / close)
```

— trend extension per unit of volatility, highest first, ties broken
alphabetically so the result is deterministic.

### Exit

1. **Initial stop** 2.5 ATRs below the actual fill price.
2. **Chandelier trailing stop** 3.5 ATRs below the highest close since entry,
   recomputed at every close and ratcheting upwards only.
3. A stop is checked against the *next* bar's low using the level fixed at the
   previous close. If the bar gaps through, it fills at `min(open, stop)` — the
   conservative side.
4. **Trend break**: close below the 200-day average exits at the next open.
5. A position opened at today's open is already stop-checked on that same bar, so
   a gap down after entry can stop out same-day.

### Position sizing

```
stop_distance   = 2.5 × ATR
shares_by_risk  = (equity × 0.75%) / (stop_distance × fx)
shares_by_size  = (equity × 25%)  / (close × fx)
shares_by_liq   = 5% × 20-day average volume
shares          = floor(min of the three))
```

At most 6 concurrent positions and 6 % total open risk. Orders that do not fit
the cash balance are truncated or dropped — **and logged into the report**. A
strategy whose results depend on positions the account could not afford is a bug
report, not an edge.

Note one deliberate imperfection: sizing uses the signal bar's close while the
order fills at the next open, so realized risk deviates slightly from 0.75 %.
That is what happens in reality; "fixing" it would require knowing the fill price
before it exists.

---

## How the code is put together

```
trendfolge/
  config.py          frozen dataclasses, YAML loading, configuration hashing
  universe.py        which symbols, in which currency, on which exchange
  indicators.py      SMA, Wilder ATR, average volume — all strictly causal
  datasets/
    loader.py        Yahoo / Stooq / TradingView CSV reading
    normalize.py     the canonical frame, validation, split & dividend adjustment
    fx.py            exchange rates, account currency per foreign unit
    panel.py         multi-exchange alignment  ← the important design decision
    synthetic.py     deterministic generated markets, for offline testing
  strategy.py        the entry and exit rules, pure and per-symbol
  costs.py           commission, spread, slippage
  portfolio.py       positions, the cash ledger, position sizing
  engine.py          the event-driven bar loop
  metrics.py         performance statistics
  tax.py             German capital income tax, both regimes
  benchmarks.py      buy & hold, equal weight, exposure matching
  walkforward.py     rolling re-optimization and out-of-sample stitching
  sensitivity.py     sweeps, heatmap, cost stress, bootstrap, robustness verdict
  plots.py           figures
  report.py          markdown assembly
  cli/               five entry points
```

Three decisions are worth knowing about.

**Execution bars are never forward filled.** Symbols on different exchanges have
different holidays. The portfolio walks the union of all session dates, but each
symbol's indicators are computed on its *own* bars, and the panel keeps two
separate price frames: `px_exec`, which is NaN wherever the exchange was shut and
is the only thing anything may trade against, and `px_mark`, which is forward
filled and used only to value open positions. Forward-filling the execution bars
would let the engine fill orders at stale prices on days the market was closed —
a pure, silent, optimistic bias that no equity curve reveals.

**The engine is event-driven, not vectorized.** A `signal.shift(1) × returns`
pipeline cannot express a path-dependent trailing stop, an intraday stop fill, a
position limit, a ranking among competing signals, or a cash constraint — and
every one of those omissions flatters the result. The honest version costs about
a second per twenty-year run.

**Prices are split- and dividend-adjusted by default.** Without it a four-for-one
split reads as a 75 % crash to a moving average and every ex-dividend date fires
a phantom stop. The known cost — a future dividend retroactively rescales past
prices — is documented in `normalize.py`, and a `raw` mode exists so the claim
can be checked rather than believed.

---

## Testing

```bash
python -m pytest              # around 200 tests, no network, deterministic
```

Two of them matter more than the rest.

`test_engine_lookahead.py` asserts the **prefix property**: run the backtest over
the full history, then over the history truncated at a date, and the trade list
up to that date must be identical. It also runs a **mutation test** — rewrite
every bar after a date with different numbers and the trades before it must not
move. Between them these catch nearly every way the future can leak into the
past.

`test_portfolio.py` reconstructs the final account value independently from the
trade log and the panel's own mark prices and requires it to match the engine's
figure to nine significant figures. A commission dropped or double counted
anywhere shows up immediately.

The rest cover indicator arithmetic against hand-computed values, all three CSV
dialects and their failure modes, multi-calendar order timing, currency
conversion, the exact entry bar on a scripted pullback, cost accounting to the
cent, walk-forward leakage, and a full CLI smoke run.

---

## Comparing against an ETF you would actually buy

The default universe carries **QDVE.DE** (WKN A142N1, iShares S&P 500
Information Technology, accumulating, TER 0.15 %) as a benchmark. It holds
essentially the same companies this strategy trades, which makes it the
comparison that matters: not "did technology go up" but "was running this worth
the trouble versus just buying the fund".

Three benchmarks appear in every report because they answer different questions:

| Benchmark | Question |
|---|---|
| Equal-weight of the traded universe | Did the *timing rule* add anything over holding the same shares? |
| QDVE.DE buy & hold | Should I do this at all instead of buying the ETF? |
| QDVE.DE at matched exposure | Is any lead real, or just a smaller position? |

That last one matters more than it looks. This strategy sits in cash much of the
time. Comparing the growth rate of a book invested a third of the time against a
fund invested all of the time mostly measures exposure, not skill.

### Tax is modelled, and it is the biggest single factor

The strategy holds shares **directly**: every realized gain is taxed in full, in
the year it is realized, with no Teilfreistellung. The ETF is an **equity fund**:
30 % of its return is exempt outright, and the tax on the price gain waits until
you sell — potentially for decades. Only the small Vorabpauschale is due in the
meantime.

Because the broker here is DEGIRO, a foreign broker, nothing is withheld at the
trade. Gains go into the annual return (Anlage KAP) and the bill arrives with the
assessment. That deferral is a genuine advantage over a German broker and it is
modelled (`tax.settlement: assessment`), but it does not touch the two structural
disadvantages above.

Every report shows the fund **both ways** — sold at the end of the period, and
still held with the deferred tax disclosed as a liability — because that single
assumption moves the answer more than anything else, and hiding it in a config
value would be dishonest. A long-term holder sits nearer the "held" column.

Set `tax.church_tax` if it applies to you, and `tax.settlement: withholding` if
you move to a German broker. The Basiszins table used for the Vorabpauschale is
printed in every report so you can check it against the BMF-Schreiben.

### Costs are set to a DEGIRO approximation

`configs/default.yaml` now uses EUR 2.00 + 0.026 % per order, the Xetra tariff.
The US tariff has a per-share component this model cannot express and is
approximated. **These came from broker comparison sites, not from the
Preisverzeichnis** — check them against your own before believing a result. The
sensitivity run stresses costs at 2× and 4× for exactly this reason.

---

## What would make this real — and the reasons to distrust it

Read this section as carefully as the results.

1. **A good-looking backtest is the null result, not the finding.** Trend
   following on mega-cap technology over the last fifteen years mostly measures
   the fact that mega-cap technology went up roughly tenfold. The only question
   worth answering is whether the rules beat simply owning QQQ, after costs,
   after tax, on the stitched out-of-sample curve. That comparison is the
   headline table of every report for exactly this reason.

2. **The universe is contaminated by hindsight.** `universe_megacap.yaml` was
   assembled in 2026 knowing that NVIDIA and ASML won. `universe_2010.yaml`
   exists to *measure* that bias: it holds the technology companies a person
   would plausibly have picked in January 2010 — Intel, Cisco, IBM, Nokia,
   Ericsson, Yahoo — with no knowledge of what followed. Run both. If the 2010
   universe does materially worse, then the mega-cap result is mostly
   stock-picking hindsight, and your honest expectation for live trading sits
   closer to the 2010 number. Even that list is not clean: companies that were
   acquired or delisted cannot be downloaded at all, which flatters it again.

3. **Tax is modelled, but only for one specific situation.** A German private
   investor at a foreign broker, no church tax, the €1,000 Sparerpauschbetrag,
   30 % Teilfreistellung on fund units and none on directly held shares. Change
   any of those and the comparison moves. What does *not* change is the shape of
   it: an active strategy pays the full rate every year, an ETF defers most of
   it for as long as you hold. Not tax advice — check your own situation.

4. **Currency risk is unhedged.** A euro account holding US stocks earns part of
   its return from EUR/USD. Each report decomposes every trade into the local
   move and the currency move so a currency tailwind cannot be mistaken for
   skill.

5. **Stop fills are optimistic.** Daily bars have no intraday path. The
   `min(open, stop)` rule is conservative for gaps but nothing here reproduces
   February 2018, March 2020, or a single-name earnings gap. Look at the 2× and
   4× cost stress before believing anything; a strategy that dies at twice the
   modelled friction was never real.

6. **Walk-forward does not remove overfitting, only one layer of it.** It
   controls the *parameters*. It cannot control the choice of *rules* — a
   20-day pullback in a long uptrend, on technology stocks, was picked by a human
   who already knows how the last fifteen years went. No in-sample /
   out-of-sample split can measure that.

7. **The sample is small.** A few hundred trades gives a wide standard error on
   Sharpe and Calmar. That is what the bootstrap is for: quote the 5th-to-95th
   percentile band, never the point estimate.

8. **Scope limits.** Long only, no leverage, no shorting, no options, no intraday
   data, a single-currency cash ledger, and no delisted companies.

9. **This is not financial advice**, and a backtest is not past performance — it
   is a simulation of a decision rule that nobody actually followed.

### A concrete way to decide whether it is worth anything

Run, in this order:

```bash
python -m trendfolge.cli.run_walkforward --data-dir data --out reports/wf
python -m trendfolge.cli.run_sensitivity --data-dir data --out reports/sens
python -m trendfolge.cli.run_backtest --data-dir data --out reports/bias \
    --universe configs/universe_2010.yaml
```

Then check all five of these. If any one fails, the answer is no:

- the stitched out-of-sample curve beats the benchmark on **Calmar**, not merely
  on return;
- **after tax**, the strategy beats the `after tax (held)` column of QDVE.DE —
  that is the strictest and most realistic comparison, because it lets the fund
  keep the deferral advantage a long-term holder actually gets;
- the robustness verdict says *plausibly robust* rather than *curve-fit*;
- the strategy survives the 2× cost stress with a positive growth rate;
- the 2010 universe still produces a positive out-of-sample result.

The second one is the hard one, and it is the one worth running first.

The pre-declared robustness rule is printed in every sensitivity report so it
cannot be quietly relaxed once the answer is known.
