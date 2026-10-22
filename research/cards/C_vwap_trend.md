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

## Status: blocked, needs STRATEGIES entry (0 judge runs used)
- Built: `strategies/vwap_trend.py`, spec `strategies/specs/vwap_trend.yaml`, 6 offline tests in `tests/test_strategy_vwap_trend.py` (entry on the cross with 2 ATR stop, no re-entry while above, exit on a close below VWAP, nothing before 09:45, flat before the close and no late entry, no look-ahead). All pass.
- `vwap_trend` is not in `STRATEGIES` in locked `judge/scorecard.py`. Exact entry left in `research/requests/C_strategies_entry.md`.
- Verdict: none yet. Settings stay frozen at defaults (atr_multiple 2.0); trials to pass: 1.
- Expectation, not a result: `ema_trend` (which also requires close above VWAP) did not pass, and a cross-based VWAP rule will whipsaw, so costs are the risk. Check rule 6 (break-even cost multiple) first when the run is done. The short side is untested because the engine is long only.
