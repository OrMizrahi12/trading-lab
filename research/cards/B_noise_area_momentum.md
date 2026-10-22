# Card B: Noise-area intraday momentum on SPY (and QQQ)

Paper trading only, educational use only. Not financial advice.

## Question
Does the Zarattini-Aziz-Barbon "noise area" breakout (buy when price leaves the normal same-time-of-day range around the open, trail with the band and VWAP) make money after costs on SPY/QQQ in our 18-month cache?

## Pre-registered plan
- Frozen settings (the paper's, no tuning): 14-day lookback; band = max(open, prior close) x (1 + sigma), where sigma = mean over the last 14 sessions of |close at this time of day / that day's open - 1|; long only; entries checked on 30-minute marks; exit when price falls back inside the band or below VWAP; flat before the close.
- Blocker I expect: the engine shows a strategy only the last 200 bars (about 2.5 sessions, LOOP.md lesson) but a 14-day per-slot average needs about 1,100 bars. The engine is locked. A strategy that quietly keeps its own memory across calls would not be a pure function and would break the no-look-ahead tests and the judge's re-runs, so I will not do that.
- Step 3 (cheap check, pandas only, data/cache/): a vectorised long-only version of the rule on SPY and QQQ, gross (before costs) and with an approximate cost per trade.
- PASS: all 8 judge rules on the frozen settings.
- FAIL / no strategy worth building: the cheap check shows no gross edge above costs, or the engine blocker stops the build.
- Judge runs planned: 0 if the cheap check is negative or the engine blocks; otherwise 2 (SPY, QQQ) with --trials 2.
