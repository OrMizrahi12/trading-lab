"""Offline tests for vwap_trend on hand-made bars (flat volume, so VWAP is a running mean)."""

import pandas as pd
import pytest

import indicators
from helpers import TZ, two_days
from strategies.base import Entry, Exit, Position
from strategies.vwap_trend import VwapTrend

# Today: 8 flat bars at 100, a dip to 99 (below VWAP), then a climb back above.
TODAY = [100.0] * 8 + [99.0] * 4 + [99.5, 100.0, 100.5, 101.0, 101.5] + [101.5] * 61
YEST = [100.0] * 78


def above(bars):
    c = bars["close"]
    return c > indicators.vwap(bars["high"], bars["low"], c, bars["volume"])


def held():
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 100.0, 99.0, float("inf"))


def cross_bar(bars):
    a = above(bars).to_numpy()
    return next(i for i in range(78 + 3, len(bars)) if a[i] and not a[i - 1])


def test_enters_on_the_cross_with_2_atr_stop():
    bars = two_days(YEST, TODAY)
    k = cross_bar(bars)
    sub = bars.iloc[: k + 1]
    d = VwapTrend().on_bar(sub, None)
    assert isinstance(d, Entry)
    atr = indicators.atr(sub.high, sub.low, sub.close, 14).iloc[-1]
    assert d.stop == pytest.approx(sub.close.iloc[-1] - 2 * atr)
    assert d.target == float("inf")
    assert VwapTrend().on_bar(bars.iloc[:k], None) is None


def test_no_second_entry_while_above():
    bars = two_days(YEST, TODAY)
    k = cross_bar(bars)
    assert above(bars).iloc[k + 1]
    assert VwapTrend().on_bar(bars.iloc[: k + 2], None) is None


def test_exits_on_close_below_vwap_and_holds_above():
    bars = two_days(YEST, TODAY)
    k = cross_bar(bars)
    assert VwapTrend().on_bar(bars.iloc[: k + 2], held()) is None
    down = two_days(YEST, [100.0] * 10 + [98.0])
    assert isinstance(VwapTrend().on_bar(down, held()), Exit)


def test_no_entry_before_first_signal():
    bars = two_days(YEST, [99.0, 101.0, 101.5])  # crosses at 09:35
    assert VwapTrend().on_bar(bars, None) is None


def test_flat_before_close_and_no_late_entry():
    bars = two_days(YEST, TODAY)
    s = VwapTrend()
    last = bars.iloc[: 78 + 77]  # last bar starts 15:50, closes 15:55 -> flatten bar
    assert str(last.index[-1].tz_convert(TZ).time()) == "15:50:00"
    assert isinstance(s.on_bar(last, held()), Exit)
    late = two_days(YEST, [100.0] * 8 + [99.0] * 64 + [101.0] * 6)  # crosses up at 15:30
    for n in range(78 + 73, 78 + 79):  # bars starting 15:30 on: no new entries
        assert s.on_bar(late.iloc[:n], None) is None


def test_no_look_ahead():
    bars = two_days(YEST, TODAY)
    k = cross_bar(bars)
    changed = bars.copy()
    changed.iloc[k + 1 :, changed.columns.get_loc("close")] = 50.0
    s = VwapTrend()
    assert s.on_bar(bars.iloc[: k + 1], None) == s.on_bar(changed.iloc[: k + 1], None)


def test_no_entry_before_atr_has_a_full_window():
    # a cross on the 5th bar of the data (only 5 bars exist): ATR is not defined yet, so wait instead of crashing
    today = [100.0, 100.0, 100.0, 99.0, 100.5] + [100.5] * 73
    bars = two_days(YEST, today).iloc[78:83]
    assert above(bars).to_numpy()[-1] and not above(bars).to_numpy()[-2]
    assert VwapTrend().on_bar(bars, None) is None
