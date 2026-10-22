"""Offline tests for vwap_stretch_reversion on tiny hand-made bar sequences.

Yesterday is 30 flat bars at 100 (ATR is defined from the first bar of today). Today is `n_flat`
flat bars, then the bars under test. A drop to 98.5 after 23 flat bars sits more than 2 ATR
under VWAP; 99.5 does not. The signal bar starts 09:30 + 5 * n_flat minutes.
"""

from datetime import time

import pandas as pd
import pytest

import indicators
from backtests.engine import run_backtest
from helpers import HALF_DAY1, HALF_DAY2, TZ, two_days
from strategies.base import Entry, Exit, Position
from strategies.vwap_stretch_reversion import VwapStretchReversion, VwapStretchReversionParams

FLAT = [100.0] * 30
MIDDAY = VwapStretchReversionParams(no_entry_from=time(11, 30), no_entry_until=time(14, 0))


def drop(n_flat: int = 23, close: float = 98.5, days=None) -> pd.DataFrame:
    kwargs = {"days": days} if days else {}
    return two_days(FLAT, [100.0] * n_flat + [close], **kwargs)


def held() -> Position:
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 98.5, 97.5, float("inf"))


def gap(bars: pd.DataFrame) -> float:
    """VWAP minus close, in ATRs."""
    c = bars["close"]
    vwap = indicators.vwap(bars.high, bars.low, c, bars.volume).iloc[-1]
    atr = indicators.atr(bars.high, bars.low, c, 14).iloc[-1]
    return (vwap - c.iloc[-1]) / atr


def test_close_more_than_2_atr_under_vwap_is_an_entry_with_a_2_atr_stop():
    bars = drop()
    assert gap(bars) > 2
    d = VwapStretchReversion().on_bar(bars, None)
    assert isinstance(d, Entry)
    atr = indicators.atr(bars.high, bars.low, bars.close, 14).iloc[-1]
    assert d.stop == pytest.approx(98.5 - 2 * atr)
    assert d.target == float("inf")


def test_less_than_2_atr_under_vwap_is_no_entry():
    bars = drop(close=99.5)
    assert 0 < gap(bars) < 2
    assert VwapStretchReversion().on_bar(bars, None) is None


def test_the_stretch_is_measured_from_todays_vwap_only():
    # Yesterday trades far higher: a VWAP that leaked across days would make 100 look stretched.
    bars = two_days([110.0] * 30, [100.0] * 24)
    assert abs(gap(bars)) < 0.5
    assert VwapStretchReversion().on_bar(bars, None) is None


def test_exit_when_the_close_gets_back_to_vwap():
    bars = two_days(FLAT, [100.0] * 23 + [98.5, 100.0])
    c = bars["close"]
    assert c.iloc[-1] >= indicators.vwap(bars.high, bars.low, c, bars.volume).iloc[-1]
    s = VwapStretchReversion()
    assert s.on_bar(bars, held()) == Exit("vwap")
    below = two_days(FLAT, [100.0] * 23 + [98.5, 99.9])  # just under VWAP: keep holding
    assert gap(below) > 0
    assert s.on_bar(below, held()) is None


def test_first_signal_and_last_entry_limits_apply():
    bars = drop()  # signal bar starts 11:25
    assert isinstance(VwapStretchReversion().on_bar(bars, None), Entry)
    late = VwapStretchReversionParams(first_signal=time(11, 30))
    assert VwapStretchReversion(late).on_bar(bars, None) is None
    assert VwapStretchReversion(VwapStretchReversionParams(
        last_entry_minutes_before_close=275)).on_bar(bars, None) is None  # 16:00 - 275 = 11:25
    assert isinstance(VwapStretchReversion(VwapStretchReversionParams(
        last_entry_minutes_before_close=270)).on_bar(bars, None), Entry)
    early = two_days(FLAT, [99.0, 98.0, 96.0])  # first three bars: no signal whatever the gap
    assert VwapStretchReversion().on_bar(early, None) is None


def test_midday_window_blocks_the_entry_but_not_the_exit():
    assert VwapStretchReversion(MIDDAY).on_bar(drop(23), None) is None  # fills 11:30
    assert isinstance(VwapStretchReversion(MIDDAY).on_bar(drop(53), None), Entry)  # starts 13:55
    exit_bars = two_days(FLAT, [100.0] * 23 + [98.5, 100.0])
    assert VwapStretchReversion(MIDDAY).on_bar(exit_bars, held()) == Exit("vwap")


def test_time_exit_on_the_bar_closing_at_1555_and_on_the_half_day_at_1255():
    s = VwapStretchReversion()
    quiet = [100.0] * 77
    assert s.on_bar(two_days(FLAT, quiet[:76]), held()) in (None, Exit("vwap"))
    assert s.on_bar(two_days(FLAT, quiet), held()) == Exit("time")  # starts 15:50
    days = (HALF_DAY1, HALF_DAY2)
    assert s.on_bar(two_days(FLAT, quiet[:41], days=days), held()) == Exit("time")  # 12:50


def test_no_entry_while_in_a_position():
    assert VwapStretchReversion().on_bar(drop(), held()) is None


def test_defaults_match_spec():
    p = VwapStretchReversionParams()
    assert (p.stretch_atr_multiple, p.atr_period, p.atr_multiple) == (2.0, 14, 2.0)
    assert (p.first_signal, p.last_entry_minutes_before_close) == (time(9, 45), 30)
    assert (p.flatten_minutes_before_close, p.no_entry_from, p.no_entry_until) == (5, None, None)


def test_engine_round_trip_enters_on_the_drop_and_exits_at_vwap():
    today = [100.0] * 23 + [98.5] + [98.5] * 3 + [100.0] + [100.0] * 50
    result = run_backtest(two_days(FLAT, today), VwapStretchReversion())
    t = result.trades
    assert list(t["reason"]) == ["vwap"]
    assert pd.Timestamp(t["exit_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") < "12:00"
