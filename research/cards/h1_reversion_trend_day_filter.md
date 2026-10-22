# Strategy card: H1, trend-day filter on the reversion strategies

Paper trading only, educational use only. Nothing here is financial advice.

- **Journal row:** H1 (score 20, the top of `research/journal.md`)
- **Date run:** 2026-10-22
- **Spec:** `strategies/specs/reversion_trend_day_filter.yaml` (plus version 2 of `bollinger_rsi_reversion.yaml` and `vwap_stretch_reversion.yaml`)
- **Code:** `strategies/session_clock.py` (`open_distance_atr`, `trend_day_blocked`), the two reversion strategies, tests in `tests/test_trend_day_filter.py`
- **Verdict: FAIL. A finished experiment, not a strategy to trade.**

## The idea in plain English

Module 8 found that the two reversion strategies (buy after a sharp drop, bet on a snap back) lose money on trend days, when the market just keeps going one way, and win on other days. But "trend day" was decided after the close, with hindsight, so it cannot be traded. H1 asks whether a rule using only what is known at the moment of the trade can switch the strategies off on those days and keep the good days.

## Rules

Base strategies unchanged (long only, fill at the next open, 2 ATR stop, no target, flat before the close). ATR is the average size of a 5-minute bar.

- `bollinger_rsi_reversion`: buy a close under the lower Bollinger band (20 bars, 2 standard deviations) with the 2-bar RSI under 10. Sell when a bar closes at or above the middle band.
- `vwap_stretch_reversion`: buy a close more than 2 ATR under VWAP (the day's volume-weighted average price). Sell when a bar closes at or above VWAP.
- **New filter:** no new entry when `abs(signal close - today's first open) / ATR(14) > 5.0`. Exits and stops are never blocked. Unknown (ATR not yet defined) counts as blocked.

## Settings

| Setting | Value | Where it came from |
|---|---|---|
| `max_open_distance_atr` | 5.0 | Fixed before any result, from the baseline entries' distance from the open only (no profit figures): the 2/3 quantile was 4.7 to 5.6 ATR. About a third to two-fifths of entries are blocked (trades fell 728 to 473, 738 to 512, 699 to 488, 653 to 424). |
| everything else | the strategies' defaults | Not tuned. |
| Judge `--trials` | 8 | Baseline and filtered, 2 strategies, 2 symbols. No second threshold was tried. |

A 14-session average daily range was the first design. I dropped it before running anything because the engine only shows a strategy the last 200 bars (about 2.5 sessions), so it would have blocked every trade.

Data: SPY and QQQ 5-minute bars, 2024-10-21 to 2026-10-21 (about 500 sessions), usual costs, 1% risk per trade.

## Result in one table (8 judge runs)

| Strategy | Symbol | Run | Trades | Sharpe | Net profit | Walk-forward Sharpe | Break-even costs | Deflated Sharpe | Noise profitable | Rules passed |
|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_rsi_reversion | QQQ | baseline | 728 | -0.14 | $-2,173 | 0.57 | 0.7x | 20.6% | 0% | 4 of 8 |
| bollinger_rsi_reversion | QQQ | filtered | 473 | -0.05 | $-694 | -0.10 | 0.8x | 23.7% | 15% | 3 of 8 |
| bollinger_rsi_reversion | SPY | baseline | 738 | -0.57 | $-5,960 | 0.16 | 0.0x | 7.6% | 0% | 2 of 8 |
| bollinger_rsi_reversion | SPY | filtered | 512 | -0.68 | $-5,300 | -1.04 | 0.0x | 4.9% | 0% | 2 of 8 |
| vwap_stretch_reversion | QQQ | baseline | 653 | -1.20 | $-18,393 | 0.44 | 0.0x | 0.9% | 0% | 1 of 8 |
| vwap_stretch_reversion | QQQ | filtered | 424 | -1.30 | $-14,265 | 0.08 | 0.0x | 0.6% | 0% | 1 of 8 |
| vwap_stretch_reversion | SPY | baseline | 699 | -1.21 | $-14,586 | -0.40 | 0.0x | 0.5% | 0% | 1 of 8 |
| vwap_stretch_reversion | SPY | filtered | 488 | -1.53 | $-13,322 | -0.74 | 0.0x | 0.1% | 0% | 1 of 8 |

## Verdict: FAIL

The filter does not rescue either strategy. Plain reading:

- **The base strategies already lose after costs.** All four unfiltered runs lose money after costs, and only Bollinger on QQQ is profitable at zero costs (+$4,061 over two years). The filter cannot fix a strategy whose gross edge is smaller than its costs: break-even is 0.7x to 0.8x of the usual costs on the best run and 0.0x on the other seven.
- **The filter does what it says on the surface, but not the useful thing.** It blocks 30% to 35% of trades, and the net loss shrinks in all four pairs (fewer trades, fewer costs). The Sharpe improves in only one of four (Bollinger QQQ, -0.14 to -0.05, still far below the 1.0 bar). At zero costs the one run that made money fell from $4,061 to $3,383, so the trades the filter removed were slightly profitable before costs, not losers.
- **The module 8 trend-day effect did not turn into a tradable filter.** Module 8's split (profit factor 0.43 to 0.68 on trend days, 1.18 to 1.71 on others) used the finished day's open-to-close move. "5 ATR from the open at the signal bar" is a different, earlier and noisier thing. This test does not show the effect is absent; it shows this proxy does not capture it.
- **Walk-forward Sharpe got worse with the filter** on all 4 runs. Re-tuning (Optuna) is not given the filter, so this is the strategy's other settings failing to find an edge on the remaining trades.
- **Deflated Sharpe** is under 24% everywhere with only 8 trials counted. Nothing is close to the 95% bar.
- Read with the caution that this is two years (about 500 sessions) and the same two ETFs used everywhere in the lab.

## Full scorecard: bollinger_rsi_reversion on QQQ with the filter (the best of the eight)

```
SCORECARD  bollinger_rsi_reversion on QQQ 5min  {'max_open_distance_atr': 5.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 283 s

#  rule                              result     needs   verdict
1  Trades                               473    >= 200   PASS
2  Sharpe after costs                 -0.05    >= 1.0   FAIL
3  Walk-forward Sharpe                -0.10    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.58    >= 0.5   PASS
5  Monte Carlo drawdown, P5          -14.3%   >= -20%   PASS
6  Break-even cost multiple            0.8x   >= 2.0x   FAIL
7  Deflated Sharpe                    23.7%    >= 95%   FAIL
8  Parameter noise                      15%    >= 80%   FAIL

  2. net $-694, max drawdown -6.9%
  3. 4 of 9 traded windows made money, tuning Sharpe averaged 1.06
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -6.9%
  6. net profit at 0/1/2/3/5x costs: $3,383, $-694, $-4,600, $-8,347, $-15,431
  7. after 8 trials, bar to beat 0.46
  8. 6 of 40 nudged versions profitable, Sharpe P5 -0.85, median -0.29

OVERALL: FAIL (3 of 8 rules passed)
```

## Next step

1. **Stop H1 here.** Trying other thresholds (3, 4, 6 ATR) or other distance measures on the same two years would be fishing: each try adds to the trial count and none of the rules besides noise would plausibly flip, because the root problem is costs, not trend days.
2. **If reversion is revisited, attack costs first**, not regimes: a rarer, deeper stretch (fewer trades with a larger average move) so a trade clears its costs. That is a different idea and needs a new journal row.
3. **Move on to H2 (time exit for ORB)**, the next in the journal order. ORB is the only lab strategy with a signal that survives the costs.
4. **Cheap diagnostic if wanted later:** a pandas-only check of how well "distance from the open at 11:00" predicts the day's final open-to-close move, to see whether any live-knowable trend-day signal exists at all.

## Notes on the experiment

- Tests: `uv run pytest` passes (181 tests). The filter has its own tests: the distance in ATRs, no look-ahead, gaps not counted as a move, unknown history blocked, exits never blocked, filter off by default.
- The judge gained `vwap_stretch_reversion` in `STRATEGIES` (tunable: `stretch_atr_multiple`, `atr_multiple`). This card's runs used `--trials 8`.
- Reproduce: `uv run -m judge.scorecard bollinger_rsi_reversion --symbol QQQ --set max_open_distance_atr=5.0 --trials 8` (drop the `--set` for the baseline). About 2 to 5 minutes per run.

## Appendix: the other seven scorecards (verbatim)


```
SCORECARD  bollinger_rsi_reversion on QQQ 5min  (default settings)
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 118 s

#  rule                              result     needs   verdict
1  Trades                               728    >= 200   PASS
2  Sharpe after costs                 -0.14    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.57    >= 0.5   PASS
4  Fixed-settings walk-forward         0.55    >= 0.5   PASS
5  Monte Carlo drawdown, P5          -18.9%   >= -20%   PASS
6  Break-even cost multiple            0.7x   >= 2.0x   FAIL
7  Deflated Sharpe                    20.6%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-2,173, max drawdown -10.1%
  3. 4 of 9 traded windows made money, tuning Sharpe averaged 1.09
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -10.1%
  6. net profit at 0/1/2/3/5x costs: $4,061, $-2,173, $-8,018, $-13,505, $-23,570
  7. after 8 trials, bar to beat 0.44
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.04, median -0.52

OVERALL: FAIL (4 of 8 rules passed)
```

```
SCORECARD  bollinger_rsi_reversion on SPY 5min  {'max_open_distance_atr': 5.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 242 s

#  rule                              result     needs   verdict
1  Trades                               512    >= 200   PASS
2  Sharpe after costs                 -0.68    >= 1.0   FAIL
3  Walk-forward Sharpe                -1.04    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.27    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -15.1%   >= -20%   PASS
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     4.9%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-5,300, max drawdown -9.1%
  3. 4 of 9 traded windows made money, tuning Sharpe averaged 0.56
  4. 5 of 9 traded windows made money, no re-tuning
  5. real max drawdown -9.1%
  6. net profit at 0/1/2/3/5x costs: $-1,533, $-5,300, $-8,943, $-12,457, $-19,033
  7. after 8 trials, bar to beat 0.48
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.87, median -1.28

OVERALL: FAIL (2 of 8 rules passed)
```

```
SCORECARD  bollinger_rsi_reversion on SPY 5min  (default settings)
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 110 s

#  rule                              result     needs   verdict
1  Trades                               738    >= 200   PASS
2  Sharpe after costs                 -0.57    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.16    >= 0.5   FAIL
4  Fixed-settings walk-forward         0.43    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -17.9%   >= -20%   PASS
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     7.6%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-5,960, max drawdown -10.0%
  3. 4 of 9 traded windows made money, tuning Sharpe averaged 0.47
  4. 7 of 9 traded windows made money, no re-tuning
  5. real max drawdown -10.0%
  6. net profit at 0/1/2/3/5x costs: $-506, $-5,960, $-11,139, $-16,018, $-25,011
  7. after 8 trials, bar to beat 0.40
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.57, median -1.13

OVERALL: FAIL (2 of 8 rules passed)
```

```
SCORECARD  vwap_stretch_reversion on QQQ 5min  {'max_open_distance_atr': 5.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 305 s

#  rule                              result     needs   verdict
1  Trades                               424    >= 200   PASS
2  Sharpe after costs                 -1.30    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.08    >= 0.5   FAIL
4  Fixed-settings walk-forward        -0.57    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -26.3%   >= -20%   FAIL
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     0.6%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-14,265, max drawdown -16.3%
  3. 6 of 9 traded windows made money, tuning Sharpe averaged 0.81
  4. 3 of 9 traded windows made money, no re-tuning
  5. real max drawdown -16.3%
  6. net profit at 0/1/2/3/5x costs: $-11,150, $-14,265, $-17,253, $-20,150, $-25,616
  7. after 8 trials, bar to beat 0.36
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.36, median -1.08

OVERALL: FAIL (1 of 8 rules passed)
```

```
SCORECARD  vwap_stretch_reversion on QQQ 5min  (default settings)
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 225 s

#  rule                              result     needs   verdict
1  Trades                               653    >= 200   PASS
2  Sharpe after costs                 -1.20    >= 1.0   FAIL
3  Walk-forward Sharpe                 0.44    >= 0.5   FAIL
4  Fixed-settings walk-forward        -0.58    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -33.2%   >= -20%   FAIL
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     0.9%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-18,393, max drawdown -21.0%
  3. 5 of 9 traded windows made money, tuning Sharpe averaged 0.63
  4. 4 of 9 traded windows made money, no re-tuning
  5. real max drawdown -21.0%
  6. net profit at 0/1/2/3/5x costs: $-13,796, $-18,393, $-22,775, $-26,927, $-34,568
  7. after 8 trials, bar to beat 0.25
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.19, median -0.98

OVERALL: FAIL (1 of 8 rules passed)
```

```
SCORECARD  vwap_stretch_reversion on SPY 5min  {'max_open_distance_atr': 5.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 310 s

#  rule                              result     needs   verdict
1  Trades                               488    >= 200   PASS
2  Sharpe after costs                 -1.53    >= 1.0   FAIL
3  Walk-forward Sharpe                -0.74    >= 0.5   FAIL
4  Fixed-settings walk-forward        -0.60    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -23.1%   >= -20%   FAIL
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     0.1%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-13,322, max drawdown -14.2%
  3. 3 of 9 traded windows made money, tuning Sharpe averaged 0.60
  4. 4 of 9 traded windows made money, no re-tuning
  5. real max drawdown -14.2%
  6. net profit at 0/1/2/3/5x costs: $-10,062, $-13,322, $-16,521, $-19,565, $-25,302
  7. after 8 trials, bar to beat 0.43
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.81, median -1.34

OVERALL: FAIL (1 of 8 rules passed)
```

```
SCORECARD  vwap_stretch_reversion on SPY 5min  (default settings)
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 231 s

#  rule                              result     needs   verdict
1  Trades                               699    >= 200   PASS
2  Sharpe after costs                 -1.21    >= 1.0   FAIL
3  Walk-forward Sharpe                -0.40    >= 0.5   FAIL
4  Fixed-settings walk-forward        -0.55    >= 0.5   FAIL
5  Monte Carlo drawdown, P5          -26.9%   >= -20%   FAIL
6  Break-even cost multiple            0.0x   >= 2.0x   FAIL
7  Deflated Sharpe                     0.5%    >= 95%   FAIL
8  Parameter noise                       0%    >= 80%   FAIL

  2. net $-14,586, max drawdown -15.1%
  3. 3 of 9 traded windows made money, tuning Sharpe averaged 0.18
  4. 2 of 9 traded windows made money, no re-tuning
  5. real max drawdown -15.1%
  6. net profit at 0/1/2/3/5x costs: $-9,842, $-14,586, $-19,037, $-23,272, $-31,024
  7. after 8 trials, bar to beat 0.31
  8. 0 of 40 nudged versions profitable, Sharpe P5 -1.45, median -1.20

OVERALL: FAIL (1 of 8 rules passed)
```
