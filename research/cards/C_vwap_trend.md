# Card C: VWAP trend (long above VWAP) on QQQ

Paper trading only, educational use only. Not financial advice. VWAP = session volume-weighted average price, the average price paid today.

## Question
Does simply being long QQQ while a 5-minute bar closes above VWAP (and out when it closes below) make money after costs in our 18-month cache?

## Pre-registered plan
- Strategy `vwap_trend` (new, long only). Enter on the bar where the close crosses from at-or-below to above VWAP (signal from 09:45 on, last entry 30 min before close); exit when a bar closes below VWAP, or at the 2 ATR stop (ATR = average bar range), or flat 5 min before the close. No target. Fills at the next open (engine).
- Frozen settings: atr_multiple 2.0 (same as ema_trend), all else default. No tuning.
- Not tested: the short side (engine is long only), so this is half of the paper's system; paper's 1-minute bars; the paper's 2018-2023 sample.
- PASS: all 8 judge rules on the frozen settings. FAIL: any rule fails.
- Judge runs planned: 1 (QQQ, defaults), `--trials 1`. Blocker expected: `vwap_trend` needs a STRATEGIES entry in locked `judge/scorecard.py`; if absent I write research/requests/C_strategies_entry.md, set Status `blocked: needs STRATEGIES entry`, and stop with 0 judge runs.

## Status: FAIL (4 of 8 rules passed; 1 distinct judge setting in the ledger)
- The STRATEGIES entry was added by the user (patch 0002, commit b4772cd). The first judge run crashed: `vwap_trend` called ATR on fewer than 15 bars at the start of a walk-forward window (`ema_trend` has a warm-up guard, this strategy did not). Fixed in 12ae2ea with a test that fails without the fix; settings unchanged, no tuning. The crashed run and the rerun use the same settings, so they are one trial.
- Scorecard, pasted unedited (command: `uv run -m judge.scorecard vwap_trend --symbol QQQ --trials 1`):

```
SCORECARD  vwap_trend on QQQ 5min  (default settings)
data 2024-10-21 to 2026-04-21, usual costs, 1% risk per trade; took 131 s

#  rule                              result     needs   verdict
1  Trades                               995    >= 200   PASS
2  Sharpe after costs                  0.29    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.47    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.58    >= 0.5   PASS
5  Monte Carlo drawdown, P5          -19.5%   >= -20%   PASS
6  Break-even cost multiple            1.4x   >= 2.0x   FAIL
7  Deflated Sharpe                    64.8%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  2. net $3,814, max drawdown -7.1%
  3. 3 of 6 traded windows made money, tuning Sharpe averaged 0.62
  4. 4 of 6 traded windows made money, no re-tuning
  5. real max drawdown -7.1%
  6. net profit at 0/1/2/3/5x costs: $13,586, $3,814, $-5,176, $-13,349, $-27,618
  7. after 1 trials (claimed), bar to beat 0.00
  8. 40 of 40 nudged versions profitable, Sharpe P5 0.13, median 0.23

OVERALL: FAIL (4 of 8 rules passed)
```

## Verdict: FAIL
- The expectation written before the run came true: the cross-based rule whipsaws. 995 trades (about 2.7 a day) earn $13,586 before costs and $3,814 after, so costs take 72% of the gross profit. Doubling costs turns it into a loss.
- It is barely above zero in every test: Sharpe 0.29, parameter-noise median 0.23. Rule 7 uses 1 trial, so its 64.8% is generous; it still fails.
- Not tested: the short side (engine is long only), 1-minute bars, the paper's 2018-2023 sample. This says the long-only 18-month version has no tradable edge, not that the paper is wrong.
- Next step: none. Overlaps with `ema_trend` (also needs close above VWAP), which also failed.
