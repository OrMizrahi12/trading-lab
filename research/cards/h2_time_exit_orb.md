# Strategy card: H2, time-based exit for the opening range breakout (ORB) on QQQ

Paper trading only, educational use only. Nothing here is financial advice.

- **Journal row:** H2
- **Goal file:** `research/goals/h2_exit.md` (attempts fixed in advance: 0 baseline, 1-3 = `max_hold_bars` 6/12/24, 4 optional)
- **Code:** optional `max_hold_bars` in `strategies/opening_range_breakout.py` (default `None` = old behavior; the entry bar counts as bar 1, the sell fills at the next open, exit reason `max_hold`). Tests in `tests/test_strategy_opening_range_breakout.py`. Judge: `max_hold_bars` added to the ORB entry in `STRATEGIES` with range (3, 36, step 1).
- **Verdict: FAIL.** A finished experiment, not a strategy to trade.

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

## Attempt 1: `max_hold_bars` = 6, 15 min range, no target (`--trials 329`)

Command: `uv run -m judge.scorecard opening_range_breakout --symbol QQQ --set range_minutes=15 --set max_hold_bars=6 --trials 329`

```
SCORECARD  opening_range_breakout on QQQ 5min  {'range_minutes': 15, 'max_hold_bars': 6}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 82 s

#  rule                              result     needs   verdict
1  Trades                               354    >= 200   PASS
2  Sharpe after costs                  1.12    >= 1.0   PASS
3  Walk-forward Sharpe                 0.41    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.81    >= 0.5   PASS
5  Monte Carlo drawdown, P5           -7.0%   >= -20%   PASS
6  Break-even cost multiple            4.0x   >= 2.0x   PASS
7  Deflated Sharpe                    66.5%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  2. net $9,258, max drawdown -3.9%
  3. 6 of 9 traded windows made money, tuning Sharpe averaged 3.09
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -3.9%
  6. net profit at 0/1/2/3/5x costs: $12,542, $9,258, $6,059, $2,950, $-2,970
  7. after 329 trials, bar to beat 0.82
  8. 40 of 40 nudged versions profitable, Sharpe P5 0.65, median 1.22

OVERALL: FAIL (6 of 8 rules passed)
```

Trades: 354. Meaning: Sharpe 1.12 clears rule 2 only just, and it is the weakest net profit ($9,258) of the passing runs; rule 3 fails.

## Attempt 2: `max_hold_bars` = 12, 15 min range, no target (`--trials 329`)

Command: `uv run -m judge.scorecard opening_range_breakout --symbol QQQ --set range_minutes=15 --set max_hold_bars=12 --trials 329`

```
SCORECARD  opening_range_breakout on QQQ 5min  {'range_minutes': 15, 'max_hold_bars': 12}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 76 s

#  rule                              result     needs   verdict
1  Trades                               354    >= 200   PASS
2  Sharpe after costs                  1.29    >= 1.0   PASS
3  Walk-forward Sharpe                 0.41    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.98    >= 0.5   PASS
5  Monte Carlo drawdown, P5           -7.8%   >= -20%   PASS
6  Break-even cost multiple        above 5x   >= 2.0x   PASS
7  Deflated Sharpe                    76.3%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  2. net $13,413, max drawdown -7.2%
  3. 6 of 9 traded windows made money, tuning Sharpe averaged 3.09
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -7.2%
  6. net profit at 0/1/2/3/5x costs: $16,835, $13,413, $10,074, $6,848, $595
  7. after 329 trials, bar to beat 0.79
  8. 40 of 40 nudged versions profitable, Sharpe P5 0.64, median 1.04

OVERALL: FAIL (6 of 8 rules passed)
```

Trades: 354. Meaning: The best of the three (Sharpe 1.29, rule 4 at 0.98), but still short of the 1R baseline's 1.77 and it fails rule 3 and the Deflated Sharpe.

## Attempt 3: `max_hold_bars` = 24, 15 min range, no target (`--trials 329`)

Command: `uv run -m judge.scorecard opening_range_breakout --symbol QQQ --set range_minutes=15 --set max_hold_bars=24 --trials 329`

```
SCORECARD  opening_range_breakout on QQQ 5min  {'range_minutes': 15, 'max_hold_bars': 24}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 74 s

#  rule                              result     needs   verdict
1  Trades                               354    >= 200   PASS
2  Sharpe after costs                  0.88    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.41    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.83    >= 0.5   PASS
5  Monte Carlo drawdown, P5          -11.1%   >= -20%   PASS
6  Break-even cost multiple            4.3x   >= 2.0x   PASS
7  Deflated Sharpe                    59.1%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  2. net $10,453, max drawdown -6.1%
  3. 6 of 9 traded windows made money, tuning Sharpe averaged 3.09
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -6.1%
  6. net profit at 0/1/2/3/5x costs: $13,809, $10,453, $7,190, $4,043, $-1,984
  7. after 329 trials, bar to beat 0.71
  8. 40 of 40 nudged versions profitable, Sharpe P5 0.46, median 0.86

OVERALL: FAIL (5 of 8 rules passed)
```

Trades: 354. Meaning: Holding two hours fails rule 2 (0.88) and has the deepest drawdown of the three: the longer the hold, the less the edge.

## Verdict: FAIL (per `research/goals/h2_exit.md`)

All three time exits fail rule 3 (walk-forward Sharpe 0.41 vs 0.5 needed), which is a stated FAIL condition. Attempt 4 (best of 1-3 on SPY) was optional and skipped: the verdict was already decided, so running it would only add to the trial count. Plain reading:

| Run | Sharpe (rule 2) | Rule 3 | Fixed WF (rule 4) | Break-even costs | Deflated Sharpe | Noise | Net profit | Rules passed |
|---|---|---|---|---|---|---|---|---|
| 0: 1R target | 1.77 | 0.41 FAIL | 1.50 | above 5x | 87.6% | 100% | $24,671 | 6 of 8 |
| 1: 6 bars | 1.12 | 0.41 FAIL | 0.81 | 4.0x | 66.5% | 100% | $9,258 | 6 of 8 |
| 2: 12 bars | 1.29 | 0.41 FAIL | 0.98 | above 5x | 76.3% | 100% | $13,413 | 6 of 8 |
| 3: 24 bars | 0.88 FAIL | 0.41 FAIL | 0.83 | 4.3x | 59.1% | 100% | $10,453 | 5 of 8 |

- **The time exit does not beat the 1R target.** On every number (Sharpe, net profit, Deflated Sharpe, fixed walk-forward) the 1R baseline is better, even though 1R is the hand-picked setting H2 hoped to avoid. The idea that profit is made early and fades did not turn into a better exit; a fixed hold just sells winners and losers on a clock.
- **Where it does work:** rules 1, 4, 5, 6 and 8 pass for all three, and 6 and 12 bars make money after costs with 40 of 40 noisy versions profitable. There is something here (the edge is not only the target), but it is smaller than the baseline's and not strong enough to pass.
- **Rule 3 is identical in all four runs (0.41, same 6 of 9 windows, tuning Sharpe 3.09).** The walk-forward tunes `range_minutes`, `target_r` and `max_hold_bars` itself and ignores the command-line value of the time exit, so it cannot tell the four runs apart. This is a limit of the test as set up (the goal file said to add the setting to `STRATEGIES` and not to touch rule logic), not evidence about the time exit. Consequence: for any ORB variant on this judge, rule 3 is decided by the tuner, so H2 as defined could never have passed rule 3 differently from the baseline. A follow-up would need the judge to be able to hold a setting fixed during walk-forward; that is a rule change and needs its own decision.
- **Deflated Sharpe** needs 95% and the best run reaches 76%, with 329 trials counted. Only the baseline (87.6%) is close, and it too is under the bar.
- Two years (about 500 sessions) of QQQ, the same data used everywhere in the lab.

## Next step

1. **Stop H2 here.** No other hold lengths (8, 10, 18 bars) and no SPY run: that would be fishing and each try raises the trial count.
2. **Open question for a new journal row:** a judge option to freeze a setting in walk-forward, so a fixed-rule variant is judged on the strategy and not on the tuner's choice. Needs a decision, since it changes rule logic.
3. Back to the journal order for the next idea.

## Notes

- Tests: `uv run pytest` passes (183 tests). New tests: the time exit fires at `max_hold_bars` and never by default, a run with `max_hold_bars=None` is identical to the old one, and a 6-bar hold sells exactly 30 minutes after the entry fill.
- Never-change list respected: nothing touched in `judge/standards.yaml`, rule logic, `backtests/engine.py`, `CostModel`, `backtests/sizing.py`, `data/cache/`, the ORB entry logic, the 6/12/24 grid or the trial counts.
