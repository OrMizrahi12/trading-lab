# Card A: Last-half-hour momentum

Paper trading only, educational use only. Nothing here is financial advice.

## Question
Does the return from the prior close to 30 minutes before the real close predict the return of the last 30 minutes on SPY and QQQ (18 months, 5-minute bars)? If yes, is a long-only version worth judging?

## Pre-registered plan
- Statistic (pandas, `data/cache/` only): x = price at (real close - 30 min) / prior session close - 1. y = close / price at (real close - 30 min) - 1. Half days use 13:00 as the close. Report correlation, mean y when x>0 vs x<=0, number of days, and a shuffled-label test (1,000 shuffles of y against x). Also the "volatile day" variant: x>0 and |x| above the median |x|. Also first half-hour variant (open to 10:00 return). That is 3 signals; each counts as a trial.
- Gate: build a strategy only if the long-only mean y after x>0 is positive, the shuffle p-value is under 0.05 on at least one of SPY/QQQ, and the mean gross gain is larger than a rough round-trip cost (about 2 bps spread plus slippage). Otherwise verdict is "no strategy worth building" and I stop.
- If built: `last_half_hour_momentum`, frozen settings: signal at the 15:25 bar close, fill 15:30 open, flatten at the close, threshold 0 (up since prior close). Judge on QQQ with 1 to 2 runs, `--trials` = max(ledger, 2). FAIL if any of the 8 rules fails; PASS only if all 8 pass.
- Not tested: the short side (engine cannot short), so only half the paper's effect.

## Result of the cheap check (script: `research/cards/A_last_half_hour_stat.py`, run with `uv run python`)
374 sessions each, 5-minute bars, New York time via the NYSE calendar's real close (half days included; sessions with missing bars skipped). Signal = price 30 min before the close vs the prior close; y = return of the last 30 minutes (fill at that bar's open to the last close). "p" is the share of 1,000 shuffles whose mean y after the signal was at least as high as the real one (small p = better than chance).

| Symbol | Signal | days | mean y after signal (bps) | mean y otherwise (bps) | p | hit rate |
|---|---|---|---|---|---|---|
| SPY | up since prior close | 207 | -0.74 | +1.64 | 0.84 | 0.44 |
| SPY | up and bigger than median move | 109 | -2.50 | +1.49 | 0.93 | 0.42 |
| SPY | up in first 30 min | 202 | -0.02 | +0.74 | 0.63 | 0.46 |
| QQQ | up since prior close | 208 | -1.45 | +5.13 | 0.98 | 0.50 |
| QQQ | up and bigger than median move | 111 | -4.26 | +3.89 | 1.00 | 0.41 |
| QQQ | up in first 30 min | 204 | +1.16 | +1.85 | 0.60 | 0.52 |

Correlation of x with y: SPY 0.008 (since prior close) and 0.06 (first half-hour); QQQ -0.022 and 0.05. (1 bp = 0.01%, about the size of one spread cost.)

## Verdict: no strategy worth building (0 judge runs, 0 new strategies)
The gate failed: after an up signal the long-only mean is zero or negative on both symbols and no p-value is under 0.05. The sign is, if anything, opposite (a fade), but with 3 signals x 2 symbols the best "reversal" p of about 0.02 (QQQ, up since prior close) is not a finding. The paper's effect is not visible in SPY/QQQ over 2024-10 to 2026-04, in line with replications that found it weaker after the paper's sample ended. Caveat: 374 days cannot detect a true effect of 1 to 2 bps, and that size would be eaten by costs anyway. The short side was not tested (engine cannot short).

## Lessons
- 6 statistics tried, no judge runs, so nothing was added to the trial ledger.
- The strategy would not need more than 200 bars (prior close is within 2.5 sessions), so history was not the obstacle; the statistic is.
- Next step: none. A fade (short) version needs engine shorting and has no solid statistic to justify the request.
