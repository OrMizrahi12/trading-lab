# Goal: H2, does a time-based exit fix the opening range breakout (ORB) on QQQ?

Journal idea H2 (`research/journal.md`). Paper trading only, nothing here is financial advice.

**Question.** Replace the 1R profit target with "sell N 5-minute bars after entry" and test whether the edge survives
the lab's judge without a hand-picked R target.

**Who decides.** Only `uv run -m judge.scorecard` (8 rules, thresholds in `judge/standards.yaml`). Your opinion of the
numbers does not count; the exit code and the printed PASS/FAIL lines do.

**Allowed changes (only these).**
1. Add an optional `max_hold_bars` (default `None` = old behavior) to `strategies/opening_range_breakout.py`, with a test
   in `tests/` showing a run without it is unchanged.
2. Add the setting to the `STRATEGIES` entry in `judge/scorecard.py` (settings and ranges only, no rule logic).
3. Write the result card and update the H2 row in `research/journal.md`.

**Attempts (fixed now, no others allowed).** At most 5 scorecard runs, all on QQQ 5min, ORB 15-minute range, no VWAP
filter, `target_r` unset, `--trials 329` (326 earlier ORB runs + 3 new settings):
- Attempt 0: baseline, 15 min / 1R target (`--trials 326`), to have a number to compare against.
- Attempts 1-3: `max_hold_bars` = 6, 12, 24. Run all three even if one passes (we judge plateau, not peak).
- Attempt 4 (optional): the best of 1-3 on SPY with the same settings, no re-tuning.

**Done means one of:**
- PASS: some `max_hold_bars` passes all 8 rules AND at least one neighbouring value (6/12/24) passes rule 2
  (Sharpe after costs, at least 1.0). A lone winner is a peak, not a pass.
- FAIL: all 5 attempts used, or all three values fail rule 2 or 3 (full-data or walk-forward Sharpe). Either is a valid
  finish; a failed idea gets a card too.

**Record after every attempt** (append to `research/cards/h2_time_exit_orb.md`, then commit straight to `main`):
the exact command, settings, `--trials`, all 8 rules as value + PASS/FAIL, trade count, and one sentence on what it
means. Record failures as carefully as passes.

**Never change:** `judge/standards.yaml`, any scorecard rule or test setting, `backtests/engine.py`, `CostModel`,
`backtests/sizing.py`, `data/cache/`, the ORB entry logic, the 6/12/24 grid, the trial count (never lower it), or the
settings after seeing a result. No real orders, brokers or credentials.
