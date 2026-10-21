"""Hand-made bar builders shared by the strategy tests.

A bar opens at the previous close and has a 0.2 wick each way, so one flat bar's range is
0.4. Bar `i` of a day starts at 09:30 + 5*i minutes. Same shape as the builders in
tests/test_strategy_spy_vwap_reclaim.py.
"""

import pandas as pd

TZ = "America/New_York"
DAY1, DAY2 = "2026-03-02", "2026-03-03"  # two ordinary sessions
HALF_DAY1, HALF_DAY2 = "2025-11-26", "2025-11-28"  # day after Thanksgiving: NYSE closes 13:00


def day_bars(day: str, closes: list[float], prev_close: float, start="09:30") -> pd.DataFrame:
    first = pd.Timestamp(f"{day} {start}", tz=TZ)
    rows, prev = [], prev_close
    for c in closes:
        rows.append((prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c, 1000))
        prev = c
    idx = pd.date_range(first, periods=len(closes), freq="5min").tz_convert("UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def two_days(yesterday: list[float], today: list[float], days=(DAY1, DAY2)) -> pd.DataFrame:
    d1 = day_bars(days[0], yesterday, yesterday[0])
    return pd.concat([d1, day_bars(days[1], today, yesterday[-1])])


def start_of(bars: pd.DataFrame) -> str:
    """Local start time of the last bar, e.g. '11:25' (to label a test's scenario)."""
    return f"{bars.index[-1].tz_convert(TZ):%H:%M}"
