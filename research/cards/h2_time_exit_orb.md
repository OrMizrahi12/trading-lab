# Strategy card: H2, time-based exit for the opening range breakout (ORB) on QQQ

Paper trading only, educational use only. Nothing here is financial advice.

- **Journal row:** H2
- **Goal file:** `research/goals/h2_exit.md` (attempts fixed in advance: 0 baseline, 1-3 = `max_hold_bars` 6/12/24, 4 optional)
- **Code:** optional `max_hold_bars` in `strategies/opening_range_breakout.py` (default `None` = old behavior; the entry bar counts as bar 1, the sell fills at the next open, exit reason `max_hold`). Tests in `tests/test_strategy_opening_range_breakout.py`. Judge: `max_hold_bars` added to the ORB entry in `STRATEGIES` with range (3, 36, step 1).
- **Verdict:** (filled in at the end)

## How the judge treats the new setting (read before comparing runs)

Adding `max_hold_bars` to `STRATEGIES` means rule 3 (walk-forward) now lets Optuna tune it, along with `range_minutes` and `target_r`, in every run, including the baseline. So rule 3 in attempt 0 is not directly comparable to earlier ORB scorecards, and in attempts 1-3 rule 3 tunes the time exit and the target rather than holding the time exit fixed. Rule 4 (fixed settings on the same months) and rules 2, 5, 6, 7, 8 use the settings given on the command line. I did not change any rule logic.

## Attempt 0: baseline, 15 min range / 1R target (`--trials 326`)

Command: `uv run -m judge.scorecard opening_range_breakout --symbol QQQ --set range_minutes=15 --set target_r=1.0 --trials 326`

```
SCORECARD  opening_range_breakout on QQQ 5min  {'range_minutes': 15, 'target_r': 1.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 82 s

#  rule                              result     needs   verdict
1  Trades                               354    >= 200   PASS
2  Sharpe after costs                  1.77    >= 1.0   PASS
3  Walk-forward Sharpe                 0.41    >= 0.5   FAIL
4  Fixed-settings walk-forward         1.50    >= 0.5   PASS
5  Monte Carlo drawdown, P5           -8.9%   >= -20%   PASS
6  Break-even cost multiple        above 5x   >= 2.0x   PASS
7  Deflated Sharpe                    87.6%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  2. net $24,671, max drawdown -5.2%
  3. 6 of 9 traded windows made money, tuning Sharpe averaged 3.09
  4. 7 of 9 traded windows made money, no re-tuning
  5. real max drawdown -5.2%
  6. net profit at 0/1/2/3/5x costs: $28,200, $24,671, $21,325, $18,030, $11,648
  7. after 326 trials, bar to beat 0.94
  8. 40 of 40 nudged versions profitable, Sharpe P5 1.13, median 1.65

OVERALL: FAIL (6 of 8 rules passed)
```

Trades: 354. Meaning: the 1R baseline passes 6 of 8; it fails only the walk-forward Sharpe (0.41 vs 0.5) and Deflated Sharpe (87.6% vs 95%), which is the number the time exits have to beat.
