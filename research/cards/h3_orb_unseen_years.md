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

(empty: filled in only after the data is checked and the script is committed)

## Verdict

(empty)
