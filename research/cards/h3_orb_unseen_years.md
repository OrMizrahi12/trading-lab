# Strategy card: H3, frozen ORB 15 min / 1R on years the lab has never seen

Paper trading only, educational use only. Nothing here is financial advice.

- **Journal row:** H3
- **Status of this file: PRE-REGISTERED (written 2026-10-22, before any pre-2024-10-21 bar exists on this machine).** Everything below the line "Results" is empty on purpose. Nothing above it may be edited after the data arrives, except to fix a typo in a way a diff makes obvious.

## Question

Does the opening range breakout (ORB: buy when a 5-minute bar closes above the high of the first 15 minutes of the day, stop at the range low, take profit at 1R, flat before the close) still make money after costs on 5 years that none of its settings were ever picked on?

("R" = the amount risked, entry minus stop. "Out-of-sample" = data the settings never saw.)

## Frozen settings (no changes after the data arrives)

Strategy `strategies/opening_range_breakout.py`, spec `strategies/specs/opening_range_breakout.yaml`, code as of the commit that holds this card.

| Setting | Value |
|---|---|
| `range_minutes` | 15 |
| `target_r` | 1.0 |
| `require_above_vwap` | False |
| `max_hold_bars` | None |
| `last_entry_minutes_before_close` | 30 (default) |
| `flatten_minutes_before_close` | 5 (default) |
| Entries per day | 1 (first qualifying bar; no re-entry after a stop-out) |
| Direction | long only |
| Bars | 5-minute, regular trading hours, TRADES prices, as downloaded by `data_pipeline` |
| Costs | `CostModel` defaults (1 cent commission per share per side, 1 cent quoted spread with half per fill, 1 cent slippage on market fills) |
| Sizing | `FixedFractional`: a stop-out costs 1% of equity, capped at 100% of equity, no leverage (engine default) |
| Starting equity | $100,000 |
| Engine | `backtests.engine.run_backtest`, unchanged |

Why these: 15 min / 1R is the setting the lab's earlier work picked (best cell of the QQQ grid on 2024-10 to 2026-04). The point of H3 is to test that pick, so it is not allowed to move.

## Test (exact)

- **Symbols:** QQQ (primary), SPY (same settings, secondary). Each run exactly once. No other symbol.
- **Span:** sessions from **2019-10-01 to 2024-09-30** inclusive (New York dates), about 1,250 sessions. This ends 3 weeks before the research cache begins (2024-10-21), so no session overlaps data the settings were picked on, and the held-out last six months (from 2026-04-22) are not touched either. The bars of 2019-09-16 to 2019-09-30 and 2024-10-01 to 2024-10-20 that the backfill also fetches are **not** used.
- **Not the judge.** This is a one-off fixed-settings run, not `judge.scorecard` (the judge reads the whole cache, re-tunes in its walk-forward, and its rules were calibrated on 18 months). Not a new trial of a new setting: no `--trials` claim and nothing for the ledger. A crash or bug fix that leaves the settings alone may be rerun; any rerun is reported.
- **Missing data:** the engine skips a session whose opening range has a missing bar. Report the number of sessions in the span, sessions with a complete range, and sessions skipped. `uv run -m data_pipeline --quality` is run on the files first and its findings are copied into the card.
- **Script:** one new file, `backtests/h3_unseen_years.py`, written after the data is checked and BEFORE its first run, committed before running, reads only the out-of-sample bars, takes no settings from the command line.

## What is reported (all of it, for both symbols, even when ugly)

1. Trades, win rate, average R per trade, profit factor, net profit, CAGR, max drawdown, Sharpe and Sortino (from `backtests.metrics.compute_metrics`).
2. Monte Carlo drawdown: 5,000 draws of the real trades with replacement, as in `reports/orb_stress_test.py` (seed 42): the 5th-percentile max drawdown (the bad-luck case) and the 5th-percentile final return.
3. Calendar-year table: trades, net %, Sharpe for 2019 (Q4 only), 2020, 2021, 2022, 2023, 2024 (to Sep).
4. Cost check: net profit at 0x and 2x usual costs (the engine's `CostModel` scaled).
5. Side by side with the numbers the settings were picked on (QQQ 2024-10 to 2026-04: Sharpe 1.77, 354 trades, max drawdown -5.2%), so the drop (or not) is visible.
6. Context, not a verdict: buy-and-hold of the same symbol over the same span.

## PASS / FAIL conditions (fixed now)

Judged on **QQQ**, the pre-registered primary symbol.

- **PASS (clears the lab's bar):** Sharpe after costs >= 1.0 (same as judge rule 2) AND net profit > 0 AND Monte Carlo 5th-percentile max drawdown no worse than -20% (judge rule 5) AND at least 4 of the 6 calendar-year buckets profitable.
- **PARTIAL ("small real edge, below the lab's bar"):** not a PASS, but Sharpe >= 0.5 AND net profit > 0 AND at least 3 of 6 buckets profitable. The earlier split test ended near 0.5 to 0.85, so this is what "the pick was not pure luck, but the 1.77 was flattered" looks like.
- **FAIL:** Sharpe < 0.5, or net profit <= 0, or fewer than 3 of 6 buckets profitable. Reading: the 2024-10 to 2026-04 result was most likely luck of the pick.
- **SPY** does not change the verdict. If SPY and QQQ disagree in direction (one profitable, one not), say so in the card; the verdict stays the QQQ one.
- Expected from the earlier work (written down so I cannot rationalise later): Sharpe on unseen years somewhere between 0.3 and 1.0, with an average trade near 0.05 R. A Sharpe above 1.5 would be a surprise worth double-checking for a data or look-ahead error before believing it.

## What is NOT allowed

- Changing any setting, the span, the symbols or the conditions above after seeing a number.
- Trying 5 min, VWAP, other targets or other symbols "while the data is here". That is a different idea with its own journal row, and every variant is a trial.
- Copying pre-2024-10-21 bars back into the research use of `data/cache/` (see next section).

## Data handling (a real risk, read this)

`data_pipeline --backfill-years` writes into `data/cache/` (it extends the files backwards). Everything in the lab reads whole files from there: `judge.scorecard`, `backtests.split_validation` (its "first 12 months = tuning" would silently shift), the optimizers, the leaderboard. So after the backfill:

1. Run `uv run -m data_pipeline --quality` (offline) and copy its findings into the card.
2. **Before any other research command**, move every bar before 2024-10-21 out of `data/cache/QQQ_5min.parquet` and `SPY_5min.parquet` into `data/oos/QQQ_5min.parquet` and `data/oos/SPY_5min.parquet` (same columns, UTC index), leaving the cache exactly as it was (2024-10-21 to 2026-04-21; check the row counts 29,070 per file and the first and last timestamps). Same idea as `data/holdout/`; `data/oos/` must be added to `.gitignore`. Nothing but `backtests/h3_unseen_years.py` reads `data/oos/`.
3. Later runs of `data_pipeline` (the daily update) would not re-download that history, because the cache's oldest bar stays 2024-10-21 and plain updates only go forward.

## Results

Run once on 2026-10-22 with `backtests/h3_unseen_years.py` (committed as `ce50e8b` before its first run; no rerun, no change). Raw output: one run, numbers copied below. Reads only `data/oos/`.

**Data check** (`uv run -m data_pipeline --quality`, plus a check against the NYSE calendar):
- No duplicate timestamps, no zero or negative prices, no high below low, no close outside high-low, no bars outside session hours, no missing NYSE days, no bars on non-trading days, in either file.
- Sessions in the span: 1,258 expected and 1,258 present for both symbols (2019 Q4: 64, 2020: 253, 2021: 252, 2022: 251, 2023: 250, 2024 to Sep: 188). Every session has exactly the expected number of 5-minute bars, and all 9 half days in the span (2019-11-29, 2019-12-24, 2020-11-27, 2020-12-24, 2021-11-26, 2022-11-25, 2023-07-03, 2023-11-24, 2024-07-03) have the shortened count.
- Only gap inside a session: 2019-09-16 (QQQ starts 09:40, SPY 10:20), before the span and not used.
- **Zero-volume bars (8 per file, all March 2020, inside the span):** QQQ and SPY at 09:35 and 09:40 on 2020-03-09 and 2020-03-16, at 09:40 and 09:45 on 2020-03-12, and at 13:00 and 13:05 on 2020-03-18. Not removed or repaired. Three of the four days have them inside the 15-minute opening range, so the range high or low on those days may rest on a bar with no trades. I did not measure their effect on any result (that would need extra runs).
- Sessions with a complete 15-minute range: 1,258 of 1,258, skipped 0, for both symbols.

**QQQ (primary), usual costs, $100,000 start**

| Metric | Value |
|---|---|
| Trades | 901 |
| Win rate | 53.4% |
| Average R per trade | 0.0014 |
| Profit factor | 0.999 |
| Net profit | -$154.74 |
| CAGR | -0.03% |
| Max drawdown | -14.0% |
| Sharpe | 0.03 |
| Sortino | 0.05 |
| Exposure | 27.5% |
| Monte Carlo 5th-percentile max drawdown | -31.0% |
| Monte Carlo 5th-percentile final return | -24.0% |
| Monte Carlo median final return | -0.5% |

**SPY (secondary), same settings**

| Metric | Value |
|---|---|
| Trades | 940 |
| Win rate | 51.1% |
| Average R per trade | -0.0011 |
| Profit factor | 0.989 |
| Net profit | -$1,841.20 |
| CAGR | -0.37% |
| Max drawdown | -16.2% |
| Sharpe | -0.03 |
| Sortino | -0.05 |
| Exposure | 23.4% |
| Monte Carlo 5th-percentile max drawdown | -26.5% |
| Monte Carlo 5th-percentile final return | -21.1% |
| Monte Carlo median final return | -1.9% |

**Calendar years** (trades by entry year; net % is the account's change in that year; Sharpe from that year's daily returns)

| Bucket | QQQ trades | QQQ net % | QQQ Sharpe | SPY trades | SPY net % | SPY Sharpe |
|---|---|---|---|---|---|---|
| 2019 (Q4 only) | 45 | +1.56% | 1.60 | 48 | +2.17% | 2.88 |
| 2020 | 194 | -5.78% | -0.63 | 202 | +2.54% | 0.38 |
| 2021 | 177 | +4.97% | 0.78 | 182 | +7.36% | 1.66 |
| 2022 | 172 | -0.32% | 0.02 | 179 | -8.48% | -1.05 |
| 2023 | 179 | +3.01% | 0.48 | 184 | +0.75% | 0.19 |
| 2024 (to Sep) | 134 | -3.20% | -0.74 | 145 | -5.37% | -1.90 |

QQQ: 3 of 6 buckets profitable. SPY: 4 of 6.

**Cost check** (net profit / Sharpe)

| | 0x costs | 1x (usual) | 2x costs |
|---|---|---|---|
| QQQ | +$13,324 / 0.37 | -$155 / 0.03 | -$11,954 / -0.30 |
| SPY | +$9,244 / 0.33 | -$1,841 / -0.03 | -$11,781 / -0.40 |

With no costs at all the edge is a Sharpe of about 0.35, and the usual costs take all of it.

**Side by side, QQQ**

| | Picked on (2024-10 to 2026-04) | Unseen (2019-10 to 2024-09) |
|---|---|---|
| Sharpe | 1.77 | 0.03 |
| Trades | 354 | 901 |
| Max drawdown | -5.2% | -14.0% |

**Context: buy and hold, same span** (first open to last close; drawdown on daily closes): QQQ +157.5% (CAGR 20.8%, max drawdown -35.6%); SPY +92.8% (CAGR 14.0%, max drawdown -34.2%).

## Verdict

**FAIL** (judged on QQQ, by the rules fixed before the data existed).

- PASS needed Sharpe >= 1.0; QQQ has 0.03. Not met.
- PARTIAL needed Sharpe >= 0.5, net profit > 0 and at least 3 of 6 buckets profitable. QQQ has Sharpe 0.03 and net profit -$154.74. Not met.
- FAIL: Sharpe < 0.5 (0.03) and net profit <= 0 (-$154.74). Both triggers hit. The bucket count (3 of 6) alone would not have failed it.

Reading, as the card pre-wrote it: the 2024-10 to 2026-04 result (Sharpe 1.77) was most likely luck of the pick. The settings did slightly worse than the card's own expectation of 0.3 to 1.0 (average trade near 0.05 R; actual 0.0014 R). The Sharpe is far below the 1.5 "double-check for an error" line, so no data or look-ahead alarm.

SPY and QQQ agree in direction: both lost money after costs (SPY also a Sharpe below zero). SPY does not change the verdict.

Notes, not verdicts: before costs the strategy has a small positive edge (Sharpe about 0.35 on both symbols), and costs at the lab's usual level cancel it. The fixed-fraction sizing, stop and target were not looked at again, and per the card nothing was changed or re-run.
