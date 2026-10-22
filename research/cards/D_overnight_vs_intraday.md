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
