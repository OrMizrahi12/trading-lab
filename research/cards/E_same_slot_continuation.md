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
