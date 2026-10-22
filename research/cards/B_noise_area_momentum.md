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

## Cheap check result (research/B_cheap_check.py, cache only, run once, not tuned)
Long-only, 14-day sigma, 30-minute entry marks 10:00-15:00, above band and VWAP, exit on a close back under the band or VWAP, else at the close. Costs 5 cents a share round trip.

```
SPY trades 97 of 360 sessions | gross/share -0.185 net/share -0.235 | win% 32.0 | gross t-stat -1.55
QQQ trades 98 of 360 sessions | gross/share -0.279 net/share -0.329 | win% 26.5 | gross t-stat -1.94
```

## Verdict: no strategy worth building
- Scorecard runs used: 0. No spec, no strategy file, no STRATEGIES request.
- Before costs (gross) the long side already loses on both symbols, so costs are not the cause and no filter can rescue it (lesson "check costs first").
- Only about 97 trades in 18 months, under rule 1's 200-trade minimum, so rule 1 would fail whatever else happened.
- The exit (back inside the band or under VWAP) is tight, so ordinary wiggles shake the trade out: 27-32% winners. I did not loosen it, since that would be tuning after seeing a result.
- Engine blocker (separate and real): the engine shows a strategy only 200 bars (about 2.5 sessions), but a 14-day per-time-of-day average needs about 1,100 bars. Building it as a judged strategy needs an engine change (locked) or hidden memory inside the strategy (not a pure function). A later researcher should ask the user for a longer lookback option first.
- The paper's edge includes the short side, which the engine does not support, and its 2007-2024 sample is far longer than our cache. This is evidence that the long-only 18-month version has no edge, not a refutation of the paper.
- Not tested: shorting, the paper's exact trailing exit, other check intervals, the long history.
- Next step: none unless the engine gains a longer lookback and shorting. The "regime signal for H1" use is dropped: a signal with no gross edge makes a poor label.
