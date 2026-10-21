"""Offline tests for spy_vwap_reclaim on tiny hand-made bar sequences.

Every sequence is "yesterday" (a rising day so the 20-bar EMA sits above today's
VWAP) followed by "today": flat bars at 100, one dip bar closing under VWAP, then a
reclaim bar closing back above it. Bars are open = previous close, with a 0.2 wick
each way, so one bar's range is about 0.4 and 1.5 ATR is roughly 0.6.
"""

import pandas as pd
import pytest

import indicators
from strategies.base import Entry, Exit, Position, check_bracket
from strategies.spy_vwap_reclaim import SpyVwapReclaim, SpyVwapReclaimParams

TZ = "America/New_York"
DAY1, DAY2 = "2026-03-02", "2026-03-03"
DIP, RECLAIM = 99.8, 100.2


def day_bars(day: str, closes: list[float], prev_close: float, start="09:30") -> pd.DataFrame:
    first = pd.Timestamp(f"{day} {start}", tz=TZ)
    rows, prev = [], prev_close
    for c in closes:
        rows.append((prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c, 1000))
        prev = c
    idx = pd.date_range(first, periods=len(closes), freq="5min").tz_convert("UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def make_bars(today: list[float], start="09:30") -> pd.DataFrame:
    yesterday = [100 + i / 30 for i in range(30)]  # drifts up to about 101
    d1 = day_bars(DAY1, yesterday, yesterday[0])
    return pd.concat([d1, day_bars(DAY2, today, yesterday[-1], start)])


def pattern(n_flat: int) -> list[float]:
    """n_flat flat bars, a dip under VWAP, then the reclaim bar. Reclaim starts 09:30 + 5*(n_flat+1) min."""
    return [100.0] * n_flat + [DIP, RECLAIM]


def decide(bars: pd.DataFrame, strategy=None):
    """Decision on the last bar, with no open position."""
    return (strategy or SpyVwapReclaim()).on_bar(bars, None)


def test_entry_sets_swing_stop_and_2r_target():
    bars = make_bars(pattern(11))  # reclaim bar starts 10:30
    d = decide(bars)
    assert isinstance(d, Entry)
    # Dip bar: open 100.0, low = 99.8 - 0.2 = 99.6, so swing stop = 99.6 - 0.01.
    assert d.stop == pytest.approx(99.59)
    assert d.target == pytest.approx(100.2 + 2 * (100.2 - 99.59))


def test_stop_is_capped_at_1_5_atr():
    # Deep dip, a bar still under VWAP, then the reclaim. The swing low (97.8) would
    # be a huge stop; the reclaim bar's own low (99.7) stays above the capped stop.
    deep = [100.0] * 11 + [98.0, 99.8, 100.3]
    bars = make_bars(deep)
    d = decide(bars)
    assert isinstance(d, Entry)
    atr = indicators.atr(bars.high, bars.low, bars.close, 14).iloc[-1]
    assert d.stop == pytest.approx(100.3 - 1.5 * atr)
    assert d.stop > 97.8


def test_stop_hit_fills_at_stop_or_gap_open():
    pos = Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.2, stop=99.6, target=101.4)
    bar = pd.Series({"open": 100.0, "high": 100.1, "low": 99.5, "close": 99.7})
    assert check_bracket(pos, bar) == ("stop", 99.6)
    gap = pd.Series({"open": 99.0, "high": 99.2, "low": 98.8, "close": 99.1})
    assert check_bracket(pos, gap) == ("stop", 99.0)  # opened past the stop: worse fill


def test_target_hit_and_stop_first_when_both_touched():
    pos = Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.2, stop=99.6, target=101.4)
    up = pd.Series({"open": 100.5, "high": 101.5, "low": 100.4, "close": 101.3})
    assert check_bracket(pos, up) == ("target", 101.4)
    both = pd.Series({"open": 100.5, "high": 101.5, "low": 99.5, "close": 100.5})
    assert check_bracket(pos, both) == ("stop", 99.6)
    quiet = pd.Series({"open": 100.5, "high": 100.8, "low": 100.1, "close": 100.6})
    assert check_bracket(pos, quiet) is None


def test_time_exit_decided_on_the_bar_closing_at_1555():
    pos = Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.2, 99.6, 101.4)
    s = SpyVwapReclaim()
    flat = [100.0] * 76  # 09:30 .. 15:45 start times
    at_1545 = make_bars(flat)
    assert s.on_bar(at_1545, pos) is None
    at_1550 = make_bars(flat + [100.0])  # bar starts 15:50, closes 15:55
    assert s.on_bar(at_1550, pos) == Exit("time")


def test_first_15_minutes_blocked():
    # Reclaim on the 09:40 bar: skipped. The identical shape one bar later (09:45): taken.
    assert decide(make_bars([100.0, DIP, RECLAIM])) is None
    assert isinstance(decide(make_bars(pattern(2))), Entry)
    # Reclaim on the 09:35 bar is blocked too.
    assert decide(make_bars([DIP, RECLAIM])) is None


def rising_pattern(n_flat: int) -> list[float]:
    """A slow climb (VWAP lags it, so the EMA stays above VWAP all day), a dip, a reclaim."""
    ramp = [100 + 0.01 * i for i in range(n_flat)]
    return ramp + [ramp[-1] - 0.4, ramp[-1] + 0.1]


def test_no_entry_from_1530():
    assert isinstance(decide(make_bars(rising_pattern(70))), Entry)  # reclaim bar starts 15:25
    assert decide(make_bars(rising_pattern(71))) is None  # starts 15:30


def test_one_trade_per_day():
    s = SpyVwapReclaim()
    first = make_bars(pattern(11))
    assert isinstance(s.on_bar(first, None), Entry)
    again = make_bars(pattern(11) + [DIP, RECLAIM])
    assert s.on_bar(again, None) is None
    s.reset()
    assert isinstance(s.on_bar(again, None), Entry)


def test_no_entry_when_ema_not_above_vwap_or_already_in_position():
    # Yesterday ends near 101 so EMA > VWAP. Make today much higher so VWAP > EMA.
    high_today = [102.0] * 11 + [101.8, 102.2]
    assert decide(make_bars(high_today)) is None
    pos = Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.2, 99.6, 101.4)
    assert SpyVwapReclaim().on_bar(make_bars(pattern(11)), pos) is None


def test_defaults_match_spec():
    p = SpyVwapReclaimParams()
    assert (p.ema_period, p.atr_period, p.atr_multiple, p.swing_lookback) == (20, 14, 1.5, 10)
    assert (p.swing_buffer, p.r_multiple, p.max_trades_per_day) == (0.01, 2.0, 1)


def test_skips_when_signal_bar_low_is_under_the_capped_stop():
    # Reclaim bar opens at the deep dip's close, so its own wick sits below the ATR-capped stop.
    assert decide(make_bars([100.0] * 11 + [98.0, 100.3])) is None


def make_half_day_bars(n_flat: int) -> pd.DataFrame:
    """Same shape as make_bars, but today is 2025-11-28 (day after Thanksgiving): NYSE closes 13:00."""
    yesterday = [100 + i / 30 for i in range(30)]
    d1 = day_bars("2025-11-26", yesterday, yesterday[0])
    return pd.concat([d1, day_bars("2025-11-28", pattern(n_flat), yesterday[-1])])


def test_half_day_last_entry_is_30_minutes_before_the_1300_close():
    # Reclaim bar starts 09:30 + 5*(n_flat+1) min.
    assert isinstance(decide(make_half_day_bars(34)), Entry)  # starts 12:25, before 12:30
    assert decide(make_half_day_bars(35)) is None  # starts 12:30, inside the last 30 minutes
    # The same clock time on a normal day is fine: that close is 16:00.
    assert isinstance(decide(make_bars(pattern(35))), Entry)


def test_half_day_flattens_at_1255_not_1555():
    bars = make_half_day_bars(40)  # last bar starts 13:05; walk back to the bars ending 12:50 / 12:55
    pos = Position(pd.Timestamp("2025-11-28 15:00", tz="UTC"), 100.2, 99.6, 101.4)
    ends_1255 = bars[bars.index.tz_convert(TZ) <= pd.Timestamp("2025-11-28 12:50", tz=TZ)]
    assert isinstance(SpyVwapReclaim().on_bar(ends_1255, pos), Exit)  # bar 12:50-12:55
    assert SpyVwapReclaim().on_bar(ends_1255.iloc[:-1], pos) is None  # bar 12:45-12:50
    # Normal day, same clock time: still holding.
    normal = make_bars(pattern(40))
    assert SpyVwapReclaim().on_bar(normal, pos) is None
