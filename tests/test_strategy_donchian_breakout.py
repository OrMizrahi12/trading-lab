"""Offline tests for donchian_breakout on tiny hand-made bar sequences.

Yesterday is 30 flat bars at 100 (so the 20-bar channel and ATR are defined from the
first bar of today). Today is `n_flat` flat bars, then a breakout bar closing at 100.5.
A flat bar has high 100.2 and low 99.8, so the channel high is 100.2. The breakout bar
starts at 09:30 + 5 * n_flat minutes.
"""

from datetime import time

import pandas as pd
import pytest

import indicators
from backtests.engine import run_backtest
from helpers import HALF_DAY1, HALF_DAY2, TZ, start_of, two_days
from strategies.base import Entry, Exit, Position
from strategies.donchian_breakout import DonchianBreakout, DonchianBreakoutParams

FLAT = [100.0] * 30
MIDDAY = DonchianBreakoutParams(no_entry_from=time(11, 30), no_entry_until=time(14, 0))


def breakout(n_flat: int, close: float = 100.5, days=None) -> pd.DataFrame:
    kwargs = {"days": days} if days else {}
    return two_days(FLAT, [100.0] * n_flat + [close], **kwargs)


def held() -> Position:
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.0, 99.0, float("inf"))


def test_breakout_above_prior_20_bar_high_is_an_entry_with_the_channel_low_stop():
    bars = breakout(23)  # starts 11:25
    d = DonchianBreakout().on_bar(bars, None)
    assert isinstance(d, Entry)
    atr = indicators.atr(bars.high, bars.low, bars.close, 14).iloc[-1]
    assert 100.5 - 2 * atr < 99.8  # the ATR level is further away, so the channel low wins
    assert d.stop == pytest.approx(99.8)
    assert d.target == float("inf")


def test_close_equal_to_the_channel_high_is_not_a_breakout():
    assert DonchianBreakout().on_bar(breakout(23, close=100.2), None) is None
    assert DonchianBreakout().on_bar(breakout(23, close=100.0), None) is None


def test_signal_bar_is_not_part_of_its_own_channel():
    # The breakout bar's own high (100.7) must not count: with it included, no close could break out.
    assert isinstance(DonchianBreakout().on_bar(breakout(23, close=100.5), None), Entry)


def test_stop_is_the_2_atr_level_when_that_is_closer_than_the_channel_low():
    # A deep dip inside the last 20 bars puts the channel low far away (97.8).
    today = [100.0] * 10 + [98.0, 100.0] + [100.0] * 8 + [100.5]
    bars = two_days(FLAT, today)
    d = DonchianBreakout().on_bar(bars, None)
    assert isinstance(d, Entry)
    atr = indicators.atr(bars.high, bars.low, bars.close, 14).iloc[-1]
    assert bars.low.iloc[-20:].min() == pytest.approx(97.8)
    assert d.stop == pytest.approx(100.5 - 2 * atr)
    assert d.stop > 97.8


def test_first_signal_is_0945():
    # Breakout bar on the 09:40 bar is blocked, on the 09:45 bar it is taken.
    assert DonchianBreakout().on_bar(breakout(2), None) is None
    assert start_of(breakout(3)) == "09:45"
    assert isinstance(DonchianBreakout().on_bar(breakout(3), None), Entry)


def test_no_entry_from_1530_on_a_normal_day():
    assert isinstance(DonchianBreakout().on_bar(breakout(71), None), Entry)  # starts 15:25
    assert DonchianBreakout().on_bar(breakout(72), None) is None  # starts 15:30


def test_half_day_last_entry_is_30_minutes_before_the_1300_close():
    days = (HALF_DAY1, HALF_DAY2)
    assert isinstance(DonchianBreakout().on_bar(breakout(35, days=days), None), Entry)  # 12:25
    assert DonchianBreakout().on_bar(breakout(36, days=days), None) is None  # 12:30
    assert isinstance(DonchianBreakout().on_bar(breakout(36), None), Entry)  # normal day, same clock


def test_midday_window_blocks_fills_from_1130_up_to_but_not_including_1400():
    s = DonchianBreakout(MIDDAY)
    assert isinstance(s.on_bar(breakout(22), None), Entry)  # starts 11:20, fills 11:25
    assert s.on_bar(breakout(23), None) is None  # starts 11:25, would fill at 11:30
    assert s.on_bar(breakout(52), None) is None  # starts 13:50, would fill at 13:55
    assert isinstance(s.on_bar(breakout(53), None), Entry)  # starts 13:55, fills at 14:00
    # Default params trade all day.
    assert isinstance(DonchianBreakout().on_bar(breakout(23), None), Entry)


def test_time_exit_on_the_bar_closing_at_1555_and_on_the_half_day_at_1255():
    s = DonchianBreakout()
    flat = two_days(FLAT, [100.0] * 76)  # last bar starts 15:45
    assert s.on_bar(flat, held()) is None
    assert s.on_bar(two_days(FLAT, [100.0] * 77), held()) == Exit("time")  # starts 15:50
    days = (HALF_DAY1, HALF_DAY2)
    assert s.on_bar(two_days(FLAT, [100.0] * 40, days=days), held()) is None  # starts 12:45
    assert s.on_bar(two_days(FLAT, [100.0] * 41, days=days), held()) == Exit("time")  # 12:50


def test_midday_window_does_not_stop_a_held_position_from_exiting():
    p = DonchianBreakoutParams(no_entry_from=time(15, 0), no_entry_until=time(16, 0))
    assert DonchianBreakout(p).on_bar(two_days(FLAT, [100.0] * 77), held()) == Exit("time")


def test_no_new_entry_while_in_a_position():
    assert DonchianBreakout().on_bar(breakout(23), held()) is None


def test_defaults_match_spec():
    p = DonchianBreakoutParams()
    assert (p.channel_bars, p.atr_period, p.atr_multiple) == (20, 14, 2.0)
    assert (p.first_signal, p.last_entry_minutes_before_close) == (time(9, 45), 30)
    assert (p.flatten_minutes_before_close, p.no_entry_from, p.no_entry_until) == (5, None, None)


def synthetic_day(after_breakout: list[float]) -> pd.DataFrame:
    """78 bars: flat to 11:20, breakout at 11:25 (index 23), then `after_breakout` closes."""
    today = [100.0] * 23 + [100.5] + after_breakout
    assert len(today) == 78
    return two_days([100.0] * 78, today)


def test_engine_trend_day_rides_to_the_flatten():
    climb = [100.5 + 0.1 * i for i in range(1, 55)]
    result = run_backtest(synthetic_day(climb), DonchianBreakout())
    t = result.trades
    assert list(t["reason"]) == ["time"]
    assert t["net_pnl"].iloc[0] > 0
    assert pd.Timestamp(t["entry_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") == "11:30"
    assert pd.Timestamp(t["exit_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") == "15:55"


def test_engine_failed_breakout_is_stopped_out_at_the_channel_low():
    result = run_backtest(synthetic_day([98.0] * 54), DonchianBreakout())
    t = result.trades
    assert list(t["reason"]) == ["stop"]
    assert t["net_pnl"].iloc[0] < 0
    # Stop 99.8, sold with half the 1c spread and 1c slippage taken off the stop price.
    assert t["exit_price"].iloc[0] == pytest.approx(99.8 - 0.005 - 0.01)
