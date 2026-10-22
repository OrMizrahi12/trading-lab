# D: Overnight vs. intraday split (diagnostic)

Paper trading only, educational use only. Not financial advice.

## Question
How much of SPY's and QQQ's return in the research cache (2024-10-21 to 2026-04-21) is "overnight" (previous close to today's open) versus "intraday" (today's open to today's close), and is either part distinguishable from chance?

## Pre-registered plan
- Data: `data/cache/` SPY and QQQ 5-minute bars, New York dates, regular session only. Open = first bar's open, close = last bar's close of each session (half days included). Overnight return uses the previous session's close; a gap across missing sessions is dropped.
- Numbers: sum of log returns for each part, mean per day in basis points (1 bp = 0.01%), t-statistic, hit rate, and a 1,000-shuffle test (flip the sign of each day's return at random; how often chance beats the observed mean).
- No strategy, no settings, 0 judge runs, no `--trials`.
- Verdict if done: "diagnostic, nothing to trade". Stop.
- Meaning for the lab: intraday-only strategies can earn only the open-to-close part; the "buy-and-hold" benchmark in walk-forward includes the overnight part.

## Result (script: `research/cards/D_overnight_stat.py`, 374 sessions per symbol)
Sums are of daily log returns. t = mean / standard error; p = share of 1,000 random sign-flips whose mean beat the observed mean.

| Symbol | Part | Sum | Mean bps/day | t | Hit rate | p |
|---|---|---|---|---|---|---|
| SPY | overnight (prev close to open) | 11.30% | 3.02 | 0.84 | 0.59 | 0.21 |
| SPY | intraday (open to close) | 7.46% | 1.99 | 0.41 | 0.51 | 0.35 |
| SPY | total (+20.6% simple) | 18.75% | 5.01 | 0.90 | 0.56 | 0.20 |
| QQQ | overnight | 18.34% | 4.90 | 1.10 | 0.57 | 0.13 |
| QQQ | intraday | 7.92% | 2.12 | 0.35 | 0.54 | 0.38 |
| QQQ | total (+30.0% simple) | 26.26% | 7.02 | 1.00 | 0.57 | 0.16 |

Correlation of overnight and intraday return on the same day: -0.15 (SPY), -0.13 (QQQ), a mild tug of war as in the paper.

## Verdict: diagnostic, nothing to trade
- About 60% of SPY's and 70% of QQQ's return in the sample is overnight. An intraday-only strategy never earns it. The fair benchmark for an intraday strategy is the open-to-close part only (about +8% on QQQ over 18 months, t=0.35, indistinguishable from zero), not the +30% buy-and-hold.
- No number is significant (all p > 0.13), so the overnight premium is not proven, just present in a mostly rising 18 months (one regime).
- A long-only "hold overnight" version (buy the close, sell the open) was not built: the engine forces a flat position at the close (locked) and the effect is not significant.
- Budget used: 0 judge runs, 0 strategies, 0 trials. Next step: quote buy-and-hold open-to-close next to buy-and-hold when benchmarking.
