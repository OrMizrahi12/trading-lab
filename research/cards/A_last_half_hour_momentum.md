# Card A: Last-half-hour momentum

Paper trading only, educational use only. Nothing here is financial advice.

## Question
Does the return from the prior close to 30 minutes before the real close predict the return of the last 30 minutes on SPY and QQQ (18 months, 5-minute bars)? If yes, is a long-only version worth judging?

## Pre-registered plan
- Statistic (pandas, `data/cache/` only): x = price at (real close - 30 min) / prior session close - 1. y = close / price at (real close - 30 min) - 1. Half days use 13:00 as the close. Report correlation, mean y when x>0 vs x<=0, number of days, and a shuffled-label test (1,000 shuffles of y against x). Also the "volatile day" variant: x>0 and |x| above the median |x|. Also first half-hour variant (open to 10:00 return). That is 3 signals; each counts as a trial.
- Gate: build a strategy only if the long-only mean y after x>0 is positive, the shuffle p-value is under 0.05 on at least one of SPY/QQQ, and the mean gross gain is larger than a rough round-trip cost (about 2 bps spread plus slippage). Otherwise verdict is "no strategy worth building" and I stop.
- If built: `last_half_hour_momentum`, frozen settings: signal at the 15:25 bar close, fill 15:30 open, flatten at the close, threshold 0 (up since prior close). Judge on QQQ with 1 to 2 runs, `--trials` = max(ledger, 2). FAIL if any of the 8 rules fails; PASS only if all 8 pass.
- Not tested: the short side (engine cannot short), so only half the paper's effect.
