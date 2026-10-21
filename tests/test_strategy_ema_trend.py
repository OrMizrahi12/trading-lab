"""Offline tests for ema_trend on hand-made bar sequences.

"Up" means EMA20 > EMA50 and close > VWAP. Yesterday falls from 102 to 100, so the fast
EMA starts below the slow one. Today is 10 flat bars at 100, then a climb of 0.15 a bar:
the trend switches on partway through the morning. The tests find that bar by computing
the same condition from `indicators`, then check what the strategy does around it.
"""

from datetime import time

import pandas as pd
import pytest

import indicators
from backtests.engine import run_backtest
from helpers import HALF_DAY1, HALF_DAY2, TZ, two_days
from strategies.base import Entry, Exit, Position
from strategies.ema_trend import EmaTrend, EmaTrendParams

FALLING_DAY = [102 - 2 * i / 77 for i in range(78)]
RAMP = [100.0] * 10 + [100 + 0.15 * i for i in range(1, 69)]  # 78 bars
MIDDAY = EmaTrendParams(no_entry_from=time(11, 30), no_entry_until=time(14, 0))


def up(bars: pd.DataFrame) -> pd.Series:
    c = bars["close"]
    vwap = indicators.vwap(bars["high"], bars["low"], c, bars["volume"])
    return (indicators.ema(c, 20) > indicators.ema(c, 50)) & (c > vwap)


def held() -> Position:
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.0, 99.0, float("inf"))


def first_up_bar(bars: pd.DataFrame) -> int:
    """Position (0-based, whole frame) of the first bar of today on which `up` is true."""
    flags = up(bars).to_numpy()
    return next(i for i in range(78, len(bars)) if flags[i])


def test_enters_on_the_bar_the_trend_switches_on_with_a_2_atr_stop():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    assert 78 + 3 <= k < 78 + 60  # after 09:45 and well before the last entry
    s = EmaTrend()
    d = s.on_bar(bars.iloc[: k + 1], None)
    assert isinstance(d, Entry)
    sub = bars.iloc[: k + 1]
    atr = indicators.atr(sub.high, sub.low, sub.close, 14).iloc[-1]
    assert d.stop == pytest.approx(sub.close.iloc[-1] - 2 * atr)
    assert d.target == float("inf")
    assert s.on_bar(bars.iloc[:k], None) is None  # one bar earlier the trend was still off


def test_no_second_entry_while_the_trend_stays_on():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    assert up(bars).iloc[k + 1]  # still on a bar later, but it is not a new switch-on
    assert EmaTrend().on_bar(bars.iloc[: k + 2], None) is None


def test_needs_ema_and_vwap_conditions_together():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    c = bars["close"]
    fast, slow = indicators.ema(c, 20), indicators.ema(c, 50)
    vwap = indicators.vwap(bars.high, bars.low, c, bars.volume)
    # Just before the switch-on one of the two conditions is still missing.
    assert not ((fast.iloc[k - 1] > slow.iloc[k - 1]) and (c.iloc[k - 1] > vwap.iloc[k - 1]))
    assert EmaTrend().on_bar(bars.iloc[:k], None) is None


def test_trend_flip_exit_when_close_falls_back_under_vwap():
    ramp_then_drop = RAMP[:40] + [100.0] + RAMP[41:]
    bars = two_days(FALLING_DAY, ramp_then_drop).iloc[: 78 + 41]
    flags = up(bars)
    assert flags.iloc[-2] and not flags.iloc[-1]
    c = bars["close"]
    assert indicators.ema(c, 20).iloc[-1] > indicators.ema(c, 50).iloc[-1]  # EMAs still bullish
    s = EmaTrend()
    assert s.on_bar(bars, held()) == Exit("trend_flip")
    assert s.on_bar(bars.iloc[:-1], held()) is None  # trend still on: hold


def test_trend_flip_exit_when_the_fast_ema_drops_under_the_slow_one():
    # Rising yesterday, a gap down today, then a bar just above VWAP: the EMAs still say down.
    yesterday = [100 + 3 * i / 77 for i in range(78)]
    bars = two_days(yesterday, [97.0] * 30 + [97.3])
    c = bars["close"]
    vwap = indicators.vwap(bars.high, bars.low, c, bars.volume)
    assert c.iloc[-1] > vwap.iloc[-1]
    assert indicators.ema(c, 20).iloc[-1] <= indicators.ema(c, 50).iloc[-1]
    assert EmaTrend().on_bar(bars, held()) == Exit("trend_flip")


def test_first_signal_and_last_entry_limits_apply_to_the_switch_on_bar():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    sub = bars.iloc[: k + 1]
    t = bars.index[k].tz_convert(TZ)  # start of the switch-on bar
    assert isinstance(EmaTrend().on_bar(sub, None), Entry)
    # First signal: allowed at the bar's own start time, blocked one bar later.
    assert isinstance(EmaTrend(EmaTrendParams(first_signal=t.time())).on_bar(sub, None), Entry)
    later = (t + pd.Timedelta(minutes=5)).time()
    assert EmaTrend(EmaTrendParams(first_signal=later)).on_bar(sub, None) is None
    # Last entry: a bar must start strictly before close - N minutes.
    to_close = int((t.normalize() + pd.Timedelta(hours=16) - t).total_seconds() // 60)
    assert EmaTrend(EmaTrendParams(last_entry_minutes_before_close=to_close)).on_bar(sub, None) is None
    assert isinstance(
        EmaTrend(EmaTrendParams(last_entry_minutes_before_close=to_close - 5)).on_bar(sub, None), Entry
    )
    # The 09:45 default: no signal on any of the first three bars, whatever the indicators say.
    early = two_days(FALLING_DAY, [100.0 + 0.5 * i for i in range(3)])
    assert EmaTrend().on_bar(early, None) is None


def test_midday_window_blocks_the_switch_on_but_not_exits():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    start = bars.index[k].tz_convert(TZ)
    window = EmaTrendParams(
        no_entry_from=(start - pd.Timedelta(minutes=5)).time(), no_entry_until=time(14, 0)
    )
    assert isinstance(EmaTrend().on_bar(bars.iloc[: k + 1], None), Entry)
    assert EmaTrend(window).on_bar(bars.iloc[: k + 1], None) is None  # fill falls in the window
    ramp_then_drop = RAMP[:40] + [100.0] + RAMP[41:]
    dropped = two_days(FALLING_DAY, ramp_then_drop).iloc[: 78 + 41]
    assert EmaTrend(window).on_bar(dropped, held()) == Exit("trend_flip")


def test_time_exit_on_the_bar_closing_at_1555_and_on_the_half_day_at_1255():
    s = EmaTrend()
    climb = [100 + 0.01 * i for i in range(77)]  # stays up, so only the clock can exit
    assert s.on_bar(two_days(FALLING_DAY, climb[:76]), held()) in (None, Exit("trend_flip"))
    assert s.on_bar(two_days(FALLING_DAY, climb), held()) == Exit("time")  # starts 15:50
    days = (HALF_DAY1, HALF_DAY2)
    assert s.on_bar(two_days(FALLING_DAY, climb[:41], days=days), held()) == Exit("time")  # 12:50


def test_no_entry_while_in_a_position():
    bars = two_days(FALLING_DAY, RAMP)
    k = first_up_bar(bars)
    assert EmaTrend().on_bar(bars.iloc[: k + 1], held()) is None  # trend still on: just hold


def test_defaults_match_spec():
    p = EmaTrendParams()
    assert (p.fast_period, p.slow_period, p.atr_period, p.atr_multiple) == (20, 50, 14, 2.0)
    assert (p.first_signal, p.last_entry_minutes_before_close) == (time(9, 45), 30)
    assert (p.flatten_minutes_before_close, p.no_entry_from, p.no_entry_until) == (5, None, None)


def test_engine_trend_day_enters_on_the_switch_on_and_flattens_at_the_close():
    result = run_backtest(two_days(FALLING_DAY, RAMP), EmaTrend())
    t = result.trades
    assert list(t["reason"]) == ["time"]
    assert t["net_pnl"].iloc[0] > 0
    assert pd.Timestamp(t["exit_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") == "15:55"


def test_engine_trend_flip_closes_the_trade_before_the_close():
    # A slow fade (0.25 a bar) so the close slips under VWAP while still well above the 2 ATR stop.
    ramp_then_fade = RAMP[:40] + [RAMP[39] - 0.25 * i for i in range(1, 39)]
    result = run_backtest(two_days(FALLING_DAY, ramp_then_fade), EmaTrend())
    assert list(result.trades["reason"]) == ["trend_flip"]
    assert result.trades["exit_time"].iloc[0] < pd.Timestamp("2026-03-03 20:00", tz="UTC")
