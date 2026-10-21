"""A short pandas tour for traders, using the real SPY 5-minute bars in the cache.

Run it with:  uv run notebooks/pandas_tour.py
Paper trading / education only. Nothing here is financial advice.
"""

from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "SPY_5min.parquet"

# Show every column when printing, so the tables don't get cut with "...".
pd.set_option("display.width", 120)
pd.set_option("display.max_columns", 20)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --- 1. Load ---------------------------------------------------------------
# A bar (candle) summarises all trades in one time slice: open, high, low, close.
# The Parquet file holds one row per 5-minute bar, indexed by its start time.
bars = pd.read_parquet(CACHE)

section("1. First rows")
print(bars.head())

# --- 2. Columns ------------------------------------------------------------
# volume   = shares traded in the bar
# average  = volume-weighted average price (VWAP) of the bar
# barCount = number of individual trades in the bar
section("2. Columns, dtypes, size")
print(bars.dtypes)
print(f"{len(bars)} bars, {bars.index.min()} -> {bars.index.max()}")

# --- 3. Time zones ---------------------------------------------------------
# The cache stores UTC (a universal clock). US stocks trade 9:30-16:00 New York
# time, so convert. tz_convert only relabels the time: same instant, new clock,
# and it handles daylight saving for us.
bars.index = bars.index.tz_convert("America/New_York")

section("3. In New York time (first bar should be 09:30)")
print(bars.head(3))

# --- 4. Pick one trading day ----------------------------------------------
# .loc with a date string selects every row on that calendar day.
# We take the last day in the file (it may be a partial session).
day_str = bars.index[-1].strftime("%Y-%m-%d")
day = bars.loc[day_str]

section(f"4. One day: {day_str}")
print(f"{len(day)} bars (a full regular session has 78: 6.5 hours x 12 per hour)")
print(day[["open", "high", "low", "close", "volume"]].head())

# --- 5. Bar returns --------------------------------------------------------
# A return is the percentage change from one close to the next.
# pct_change() does (close - previous close) / previous close.
# The first row has no previous bar, so it is NaN (not-a-number = missing).
# We compute on the whole file, then slice the day, so the day's first bar
# gets a real return versus yesterday's last close (an overnight jump included).
bars["ret"] = bars["close"].pct_change()
day = bars.loc[day_str]

section("5. Bar returns for the day")
print(day[["close", "ret"]].head())
print(f"biggest up bar:   {day['ret'].max():+.4%} at {day['ret'].idxmax():%H:%M}")
print(f"biggest down bar: {day['ret'].min():+.4%} at {day['ret'].idxmin():%H:%M}")
print(f"std of 5-min returns (volatility = typical size of moves): {day['ret'].std():.4%}")

# --- 6. Resample to 15 minutes --------------------------------------------
# Resampling groups bars into bigger time buckets. For each bucket we must
# aggregate correctly: first open, max high, min low, last close, summed volume.
# (Averaging prices would be wrong.)
OHLCV = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

bars15 = bars[list(OHLCV)].resample("15min").agg(OHLCV).dropna()

section("6. 15-minute bars (same day)")
print(bars15.loc[day_str].head())
print(f"5-min bars that day: {len(day)}  ->  15-min bars: {len(bars15.loc[day_str])}")

# --- 7. Resample to daily --------------------------------------------------
# Because the index is in New York time, "1D" buckets line up with NY trading
# days, not UTC days. Weekends produce empty buckets, so we drop them.
daily = bars[list(OHLCV)].resample("1D").agg(OHLCV).dropna()

section("7. Daily bars")
print(daily.tail())

# --- 8. Proof: daily high == highest 5-minute high -------------------------
# For each day, take the max of the 5-minute highs directly (groupby on the
# calendar date) and compare to the resampled daily high. Same for the low.
direct_high = bars.groupby(bars.index.date)["high"].max()
direct_low = bars.groupby(bars.index.date)["low"].min()

# Align on plain dates so the two indexes can be compared.
daily_by_date = daily.set_index(daily.index.date)

high_ok = (daily_by_date["high"] == direct_high).all()
low_ok = (daily_by_date["low"] == direct_low).all()

section("8. Proof")
print(f"days checked: {len(daily)}")
print(f"daily high == max of 5-min highs on every day: {high_ok}")
print(f"daily low  == min of 5-min lows  on every day: {low_ok}")
assert high_ok and low_ok, "resampling disagrees with the raw bars!"

# Show the one-day case in plain numbers.
print(f"\n{day_str}: daily high {daily_by_date.loc[day.index[0].date(), 'high']:.2f} "
      f"= max 5-min high {day['high'].max():.2f} "
      f"(set in the bar at {day['high'].idxmax():%H:%M})")
