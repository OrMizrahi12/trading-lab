# Request: STRATEGIES entry for `vwap_trend` (idea C)

Paper trading only. Please add by hand to `judge/scorecard.py` (the file is locked for the agent).

Import line (next to the others):

```python
from strategies.vwap_trend import VwapTrend, VwapTrendParams
```

Entry in `STRATEGIES`:

```python
    "vwap_trend": Spec(VwapTrend, VwapTrendParams,
                       {"atr_multiple": (1.0, 4.0, None)}),
```

Why this range: the only free setting is the ATR stop distance (ATR = average bar size); 1 to 4 is the same range used for `donchian_breakout` and the reversion strategies. The entry and exit rule (cross above / close below VWAP) has no number to tune.

Then run: `scripts/research_batch.sh --only C`, or by hand
`uv run -m judge.scorecard vwap_trend --symbol QQQ --trials 1` (frozen settings = defaults, atr_multiple 2.0).
