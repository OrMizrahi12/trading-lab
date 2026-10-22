# G: Small-gap fade (card)

Paper trading only, educational use only.

**Question.** After a small gap down (open 0.10% to 0.25% under the prior close; a "gap" is the jump between yesterday's close and today's open), does price get back to the prior close the same day often enough (above ~60%) to beat costs?

**Pre-registered plan.**
- Cheap check (pandas, `data/cache/` SPY and QQQ 5min): same-day fill rate (a bar's high reaches the prior close) by gap-size bucket: 0.05-0.10, 0.10-0.25, 0.25-0.50, over 0.50 percent; also the average dollars per share gained to the target vs the engine round-trip cost, and the fill rate after a stop of 1 and 2 ATR (stop checked first within a bar).
- Rule: only if some bucket is above ~60% fill AND the average move beats round-trip cost do I write the strategy. Otherwise verdict "no strategy worth building" and stop.
- If built: frozen bucket 0.10-0.25%, entry at first bar, target prior close, stop 1.5 ATR, 1 judge run on QQQ, --trials = ledger count (at least 1). PASS only if all 8 rules pass.

## Cheap check result (script `research/g_gap_check.py`, cache only, 375 sessions, 2024-10 to 2026-04)

Fill = share of gap-down sessions where some 5-minute high reaches the prior close. "stop 0.3%" / "stop 0.6%" = target reached before price first fell that far under the open (stop wins ties). Move = average dollars per share from the open to the prior close.

```
SPY  gap size      n   fill  stop0.3%  stop0.6%  move $/sh
     0.00-0.05%   17   88%     88%       88%      0.15
     0.05-0.10%   15   93%     80%       87%      0.45
     0.10-0.25%   28   75%     68%       71%      1.16
     0.25-0.50%   40   55%     40%       48%      2.33
     over 0.50%   51   37%     24%       27%      6.72
QQQ  0.00-0.05%   12   92%     83%       83%      0.13
     0.05-0.10%    8  100%     63%       88%      0.40
     0.10-0.25%   33   64%     45%       61%      0.89
     0.25-0.50%   37   65%     46%       59%      2.08
     over 0.50%   70   41%     29%       34%      6.18
```

Round-trip cost per the engine: about 2 cents commission, 1 cent spread, 1 cent slippage on the market buy, so roughly 3 to 4 cents a share.

## Verdict: no strategy worth building

- The folk claim holds in direction: tiny gaps fill most days, big gaps do not (fill falls from about 90% to 25-40%).
- But the buckets above 60% are tiny: 0.10-0.25% has 28 (SPY) or 33 (QQQ) gap-down days in 18 months; even the whole 0 to 0.25% range is only about 60 days. The scorecard's rule 1 needs at least 200 trades, so this cannot pass by construction, and rules 3, 5 and 8 would rest on a few dozen trades. The sub-0.10% buckets (fill about 90%) move only 13-45 cents on a dozen days each.
- The one bucket with a sizeable move (SPY 0.10-0.25%, $1.16 a share, 68-75% fill) has too few days to tell skill from luck, and long-only misses the gap-up half.
- The engine fills at the next open after the first bar closes, so part of the gap is gone at entry. Not tested: the sample size already rules it out.
- No scorecard run, 0 trials added.

**Next step (if anyone revisits):** only a much longer history or many more symbols could give 200+ trades; that is a data decision for the user, not tuning.
