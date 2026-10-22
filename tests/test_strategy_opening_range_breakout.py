"""Offline tests for opening_range_breakout on tiny hand-made bar sequences.

Yesterday is 30 flat bars at 100. Today's bar `i` starts at 09:30 + 5 * i minutes. A flat bar
has high 100.2 and low 99.8, so a 5-minute opening range (bar 0) is 99.8 to 100.2.
"""

import pandas as pd
import pytest

import indicators
from backtests.engine import run_backtest
from helpers import HALF_DAY1, HALF_DAY2, TZ, start_of, two_days
from strategies.base import Entry, Exit, Position
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

FLAT = [100.0] * 30
ORB5 = OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=5))
ORB15 = OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=15))
ORB5_VWAP = OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=5, require_above_vwap=True))


def today_bars(closes: list[float], days=None) -> pd.DataFrame:
    kwargs = {"days": days} if days else {}
    return two_days(FLAT, closes, **kwargs)


def held() -> Position:
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.0, 99.0, float("inf"))


def test_5min_range_close_above_the_high_is_an_entry_with_the_range_low_stop():
    bars = today_bars([100.0, 100.5])
    assert start_of(bars) == "09:35"
    d = ORB5.on_bar(bars, None)
    assert isinstance(d, Entry)
    assert d.stop == pytest.approx(99.8)  # low of the 09:30 bar
    assert d.target == float("inf")


def test_target_r_sets_the_target_that_many_stop_distances_above_the_close():
    orb = OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=5, target_r=2.0))
    d = orb.on_bar(today_bars([100.0, 100.5]), None)
    assert d.stop == pytest.approx(99.8)
    assert d.target == pytest.approx(100.5 + 2.0 * (100.5 - 99.8))


def test_target_r_must_be_positive():
    with pytest.raises(ValueError):
        OpeningRangeBreakoutParams(target_r=0)


def test_close_at_or_below_the_range_high_is_not_a_breakout():
    assert ORB5.on_bar(today_bars([100.0, 100.2]), None) is None  # equal to the high
    assert ORB5.on_bar(today_bars([100.0, 100.1]), None) is None


def test_the_range_bar_itself_is_never_a_signal():
    # Even a first bar closing far above its own high-of-range cannot be a signal: range not done.
    assert ORB5.on_bar(today_bars([103.0]), None) is None


def test_signal_bar_high_is_not_part_of_the_range():
    # The breakout bar's own high (100.7) must not count: with it included no close could break out.
    assert isinstance(ORB5.on_bar(today_bars([100.0, 100.5]), None), Entry)


def test_15min_range_waits_for_three_bars_and_uses_all_three():
    # Bar 1 closes 100.3 (high 100.5), so the 15-minute range is 99.8 to 100.5.
    inside = [100.0, 100.3, 101.0]  # 09:40 bar closes above 100.5, but the range is still open
    assert ORB15.on_bar(today_bars(inside), None) is None
    d = ORB15.on_bar(today_bars([100.0, 100.3, 100.0, 101.0]), None)  # 09:45 bar
    assert isinstance(d, Entry)
    assert d.stop == pytest.approx(99.8)
    # 100.4 beats the 5-minute range high (100.2) but not the 15-minute one (100.5).
    assert ORB15.on_bar(today_bars([100.0, 100.3, 100.0, 100.4]), None) is None
    assert isinstance(ORB5.on_bar(today_bars([100.0, 100.4]), None), Entry)


def test_15min_range_low_is_the_lowest_low_of_the_three_bars():
    # Bar 2 dips to 99.0 (low 98.8): the stop is 98.8, not the first bar's 99.8.
    d = ORB15.on_bar(today_bars([100.0, 100.0, 99.0, 101.0]), None)
    assert isinstance(d, Entry)
    assert d.stop == pytest.approx(98.8)


def test_only_the_first_breakout_bar_of_the_day_is_a_buy():
    assert isinstance(ORB5.on_bar(today_bars([100.0, 100.5]), None), Entry)
    assert ORB5.on_bar(today_bars([100.0, 100.5, 100.6]), None) is None  # second bar above the high


def test_no_new_buy_after_a_stop_out_even_if_price_breaks_out_again():
    bars = today_bars([100.0, 100.5, 99.0, 99.5, 100.8])  # broke out, fell under the range, broke out again
    assert ORB5.on_bar(bars, None) is None


def test_a_new_day_gets_a_new_range_and_a_new_first_breakout():
    yesterday = [100.0, 100.5] + [100.0] * 28
    bars = two_days(yesterday, [100.0, 100.5])
    assert isinstance(ORB5.on_bar(bars, None), Entry)  # yesterday's own breakout does not block today


def test_vwap_variant_skips_a_breakout_that_closes_under_vwap_and_takes_a_later_one():
    # Bar 1 closes 100.0 but spikes to 104 (a long upper wick), which lifts VWAP over 100.5.
    bars = today_bars([100.0, 100.0, 100.5])
    bars.iloc[31, bars.columns.get_loc("high")] = 104.0
    v = indicators.vwap(bars.high, bars.low, bars.close, bars.volume).iloc[-1]
    assert 100.2 < 100.5 < v  # above the range high, below VWAP
    assert isinstance(ORB5.on_bar(bars, None), Entry)  # plain ORB buys this first breakout
    assert ORB5_VWAP.on_bar(bars, None) is None

    # A later close above both the range high and VWAP is the first one the variant accepts.
    later = today_bars([100.0, 100.0, 100.5, 105.0])
    later.iloc[31, later.columns.get_loc("high")] = 104.0
    assert isinstance(ORB5_VWAP.on_bar(later, None), Entry)
    assert ORB5.on_bar(later, None) is None  # plain ORB already had its first breakout at bar 2


def test_vwap_variant_still_needs_the_close_above_the_range_high():
    bars = today_bars([100.0, 100.1])  # above VWAP (about 100.05) but not above 100.2
    assert ORB5_VWAP.on_bar(bars, None) is None


def test_vwap_resets_each_day():
    # Yesterday trades far higher than today: a carried-over VWAP would block today's buy.
    yesterday = [120.0] * 29 + [100.0]  # ends at 100 so today opens without a gap
    bars = two_days(yesterday, [100.0, 100.5])
    assert isinstance(ORB5_VWAP.on_bar(bars, None), Entry)


def test_last_entry_is_30_minutes_before_the_close():
    assert isinstance(ORB5.on_bar(today_bars([100.0] * 71 + [100.5]), None), Entry)  # starts 15:25
    assert ORB5.on_bar(today_bars([100.0] * 72 + [100.5]), None) is None  # starts 15:30


def test_half_day_last_entry_is_30_minutes_before_the_1300_close():
    days = (HALF_DAY1, HALF_DAY2)
    assert isinstance(ORB5.on_bar(today_bars([100.0] * 35 + [100.5], days), None), Entry)  # 12:25
    assert ORB5.on_bar(today_bars([100.0] * 36 + [100.5], days), None) is None  # 12:30
    assert isinstance(ORB5.on_bar(today_bars([100.0] * 36 + [100.5]), None), Entry)  # normal day


def test_range_with_a_missing_bar_is_not_traded():
    bars = today_bars([100.0, 100.2, 100.5, 100.6])
    bars = bars.drop(bars.index[30 + 1])  # 09:35 bar missing: a 15-minute range has only 2 bars
    assert ORB15.on_bar(bars, None) is None


def test_time_exit_on_the_bar_closing_at_1555_and_on_the_half_day_at_1255():
    flat = today_bars([100.0] * 76)  # last bar starts 15:45
    assert ORB5.on_bar(flat, held()) is None
    assert ORB5.on_bar(today_bars([100.0] * 77), held()) == Exit("time")  # starts 15:50
    days = (HALF_DAY1, HALF_DAY2)
    assert ORB5.on_bar(today_bars([100.0] * 40, days), held()) is None  # starts 12:45
    assert ORB5.on_bar(today_bars([100.0] * 41, days), held()) == Exit("time")  # 12:50


def test_no_new_entry_while_in_a_position():
    assert ORB5.on_bar(today_bars([100.0, 100.5]), held()) is None


def test_defaults_match_spec_and_range_must_be_a_whole_number_of_bars():
    p = OpeningRangeBreakoutParams()
    assert (p.range_minutes, p.require_above_vwap) == (5, False)
    assert (p.last_entry_minutes_before_close, p.flatten_minutes_before_close) == (30, 5)
    with pytest.raises(ValueError):
        OpeningRangeBreakoutParams(range_minutes=7)
    with pytest.raises(ValueError):
        OpeningRangeBreakoutParams(range_minutes=0)


def synthetic_day(after_breakout: list[float]) -> pd.DataFrame:
    """78 bars: flat, breakout at index 10 (10:20), then `after_breakout` closes."""
    today = [100.0] * 10 + [100.5] + after_breakout
    assert len(today) == 78
    return two_days([100.0] * 78, today)


def test_engine_trend_day_rides_to_the_flatten():
    climb = [100.5 + 0.1 * i for i in range(1, 68)]
    t = run_backtest(synthetic_day(climb), OpeningRangeBreakout()).trades
    assert list(t["reason"]) == ["time"]
    assert t["net_pnl"].iloc[0] > 0
    assert pd.Timestamp(t["entry_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") == "10:25"
    assert pd.Timestamp(t["exit_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") == "15:55"


def test_engine_failed_breakout_is_stopped_at_the_range_low_and_not_bought_again():
    # Breakout, collapse under the range, then a climb back above the range high.
    result = run_backtest(synthetic_day([98.0] * 5 + [101.0] * 62), OpeningRangeBreakout())
    t = result.trades
    assert list(t["reason"]) == ["stop"]  # one trade only
    assert t["net_pnl"].iloc[0] < 0
    # Stop 99.8, sold with half the 1c spread and 1c slippage taken off the stop price.
    assert t["exit_price"].iloc[0] == pytest.approx(99.8 - 0.005 - 0.01)


def test_max_hold_bars_exits_after_that_many_bars_and_never_changes_the_default():
    orb = OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=5, max_hold_bars=6))
    bars = today_bars([100.0] * 20)
    held_n = lambda n: Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.0, 99.0, float("inf"), n)
    assert orb.on_bar(bars, held_n(5)) is None
    assert orb.on_bar(bars, held_n(6)) == Exit("max_hold")
    assert ORB5.on_bar(bars, held_n(500)) is None  # default: no time exit
    with pytest.raises(ValueError):
        OpeningRangeBreakoutParams(max_hold_bars=0)


def test_engine_run_without_max_hold_bars_is_unchanged_and_with_it_sells_after_n_bars():
    climb = [100.5 + 0.1 * i for i in range(1, 68)]
    day = synthetic_day(climb)
    base = run_backtest(day, OpeningRangeBreakout()).trades
    same = run_backtest(day, OpeningRangeBreakout(OpeningRangeBreakoutParams(max_hold_bars=None))).trades
    pd.testing.assert_frame_equal(base, same)
    t = run_backtest(day, OpeningRangeBreakout(OpeningRangeBreakoutParams(max_hold_bars=6))).trades
    assert list(t["reason"]) == ["max_hold"]
    held = pd.Timestamp(t["exit_time"].iloc[0]) - pd.Timestamp(t["entry_time"].iloc[0])
    assert held == pd.Timedelta(minutes=30)  # 6 bars
