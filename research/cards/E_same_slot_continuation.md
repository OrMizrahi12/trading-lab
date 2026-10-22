# E: Same-time-of-day continuation (statistic first)

Paper trading only, educational use only. Not financial advice.

## Question
Does a 30-minute slot's return yesterday predict the return in the same slot today (SPY, QQQ, AAPL, 5-minute bars, 2024-10-21 to 2026-04-21)?

## Pre-registered plan
- Slots: 13 half-hour slots of full 09:30-16:00 New York sessions (half days dropped). Slot return = last bar close / first bar open - 1.
- Statistic: for each slot k, z-score the slot return across days (mean 0, sd 1), then correlate today's z with the z of the same slot on the previous session (lag 1; also lags 2-5 as a check, and "different slot" pairs as a control). Report the pooled correlation over all 13 slots, per-slot correlations for the first (09:30) and last (15:30) slot, and number of observations.
- Significance: shuffle which day each return belongs to 1,000 times (permute the order of days within each slot) and count how often chance gives a pooled correlation at least as large in absolute value.
- A strategy is built ONLY if the pooled lag-1 correlation is clearly non-zero (|p| < 0.01 in at least two of three symbols, same sign, positive). Slices (per slot) are fixed above: 13 slots + pooled = 14 looks, so a single slot needs p < 0.05/14 (about 0.004) to count.
- If it fails: verdict "no strategy worth building", 0 judge runs, 0 new strategies, 0 trials. Stop.
- If it passes: long-only (engine cannot short): buy at the start of a slot after a high same-slot return yesterday; the strategy sees only 200 bars (~2.5 sessions), enough for yesterday's same slot. Then 1 strategy, judged with at most 8 runs.

## Result (script `research/cards/E_same_slot_stat.py`, output `E_same_slot_stat.txt`)
Full 09:30-16:00 sessions only: SPY 370, QQQ 370, **AAPL only 21** (its 5-minute file is short, so AAPL says nothing). Correlation of today's slot return with the same slot yesterday (z-scored per slot), shuffle test with 1,000 shuffles.

| Symbol | Pooled lag-1 corr | p | Other-slot control | Lags 2-5 |
|---|---|---|---|---|
| SPY | -0.034 | 0.011 | -0.024 | +0.033, -0.017, -0.010, +0.006 |
| QQQ | -0.021 | 0.135 | -0.021 | +0.032, -0.013, -0.006, +0.011 |
| AAPL (21 days) | -0.048 | 0.57 | +0.041 | too few days |

- The pooled sign is negative (slight reversal), the opposite of the paper's continuation, and the same size as the control that pairs a slot with a different slot yesterday (-0.02). So it is a day-level feature, not a same-slot one.
- First slot (09:30) and last slot (15:30): SPY -0.135 (p 0.008) and -0.036; QQQ -0.020 and +0.004. Not the paper's "strongest in first and last half-hour".
- Per-slot correlations swing in sign (SPY 12:30 +0.16, 13:30 +0.25, but 13:00 -0.22, 15:00 -0.30). With 13 slots x 3 symbols, a few p < 0.01 are expected by chance and no theory predicts the zig-zag. Only the 15:00 slot (-0.30 on both ETFs) passes the pre-registered 0.004 bar, and it is negative (a reversal), a single slice, so it is not traded here.
- Pre-registered gate (positive pooled correlation, p < 0.01 in two of three symbols): not met.

## Verdict: no strategy worth building
Budget used: 0 scorecard runs, 0 strategies, 0 trials. The Heston et al. continuation is not visible in SPY/QQQ over 18 months; ETFs are far more liquid than the paper's single stocks. Lesson: the AAPL 5-minute cache has only about 21 full sessions, so single-stock tests need a data download first (an IB task for the user). Next step: a 15:00 reversal would be a new, separately pre-registered idea.
