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
