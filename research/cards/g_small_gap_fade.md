# G: Small-gap fade (card)

Paper trading only, educational use only.

**Question.** After a small gap down (open 0.10% to 0.25% under the prior close; a "gap" is the jump between yesterday's close and today's open), does price get back to the prior close the same day often enough (above ~60%) to beat costs?

**Pre-registered plan.**
- Cheap check (pandas, `data/cache/` SPY and QQQ 5min): same-day fill rate (a bar's high reaches the prior close) by gap-size bucket: 0.05-0.10, 0.10-0.25, 0.25-0.50, over 0.50 percent; also the average dollars per share gained to the target vs the engine round-trip cost, and the fill rate after a stop of 1 and 2 ATR (stop checked first within a bar).
- Rule: only if some bucket is above ~60% fill AND the average move beats round-trip cost do I write the strategy. Otherwise verdict "no strategy worth building" and stop.
- If built: frozen bucket 0.10-0.25%, entry at first bar, target prior close, stop 1.5 ATR, 1 judge run on QQQ, --trials = ledger count (at least 1). PASS only if all 8 rules pass.
