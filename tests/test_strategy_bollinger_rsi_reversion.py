"""Offline tests for bollinger_rsi_reversion on tiny hand-made bar sequences.

Yesterday is 30 flat bars at 100, so the bands, RSI and ATR are defined from the first bar of
today. Today is `n_flat` flat bars, then the bars under test. With 23 flat bars before a drop to
99.0 the signal bar starts 11:25 (09:30 + 24 * 5 minutes) and the close sits below the lower
band with a 2-bar RSI of 0.
"""

from datetime import time

import pandas as pd
import pytest

import indicators
from backtests.engine import run_backtest
from helpers import HALF_DAY1, HALF_DAY2, TZ, two_days
from strategies.base import Entry, Exit, Position
from strategies.bollinger_rsi_reversion import BollingerRsiReversion, BollingerRsiReversionParams

FLAT = [100.0] * 30
MIDDAY = BollingerRsiReversionParams(no_entry_from=time(11, 30), no_entry_until=time(14, 0))


def drop(n_flat: int = 23, close: float = 99.0, days=None) -> pd.DataFrame:
    kwargs = {"days": days} if days else {}
    return two_days(FLAT, [100.0] * n_flat + [close], **kwargs)


def held() -> Position:
    return Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 99.0, 98.0, float("inf"))


def test_close_under_lower_band_with_rsi2_under_10_is_an_entry_with_a_2_atr_stop():
    bars = drop()
    c = bars["close"]
    assert c.iloc[-1] < indicators.bollinger(c)["lower"].iloc[-1]
    assert indicators.rsi(c, 2).iloc[-1] < 10
    d = BollingerRsiReversion().on_bar(bars, None)
    assert isinstance(d, Entry)
    atr = indicators.atr(bars.high, bars.low, c, 14).iloc[-1]
    assert d.stop == pytest.approx(99.0 - 2 * atr)
    assert d.target == float("inf")


def test_below_the_band_but_rsi_not_low_enough_is_no_entry():
    bars = two_days(FLAT, [100.0] * 23 + [99.0, 99.3])  # a small bounce after the drop
    c = bars["close"]
    assert c.iloc[-1] < indicators.bollinger(c)["lower"].iloc[-1]  # still under the band
    assert indicators.rsi(c, 2).iloc[-1] > 10  # but the selling has eased
    assert BollingerRsiReversion().on_bar(bars, None) is None


def test_rsi_low_but_close_still_inside_the_band_is_no_entry():
    steady_fall = [100 - 0.1 * i for i in range(1, 31)]  # every bar a loss, no sharp break
    bars = two_days(FLAT, steady_fall)
    c = bars["close"]
    assert indicators.rsi(c, 2).iloc[-1] < 10
    assert c.iloc[-1] > indicators.bollinger(c)["lower"].iloc[-1]
    assert BollingerRsiReversion().on_bar(bars, None) is None


def test_exit_when_the_close_gets_back_to_the_middle_band():
    bars = two_days(FLAT, [100.0] * 23 + [99.0, 100.0])
    c = bars["close"]
    assert c.iloc[-1] >= indicators.bollinger(c)["middle"].iloc[-1]
    s = BollingerRsiReversion()
    assert s.on_bar(bars, held()) == Exit("middle_band")
    below = two_days(FLAT, [100.0] * 23 + [99.0, 99.9])  # still just under the middle band
    assert below["close"].iloc[-1] < indicators.bollinger(below["close"])["middle"].iloc[-1]
    assert s.on_bar(below, held()) is None


def test_first_signal_and_last_entry_limits_apply():
    bars = drop()  # signal bar starts 11:25
    assert isinstance(BollingerRsiReversion().on_bar(bars, None), Entry)
    ok = BollingerRsiReversionParams(first_signal=time(11, 25))
    late = BollingerRsiReversionParams(first_signal=time(11, 30))
    assert isinstance(BollingerRsiReversion(ok).on_bar(bars, None), Entry)
    assert BollingerRsiReversion(late).on_bar(bars, None) is None
    # Last entry: a bar must start strictly before close - N minutes (16:00 - 275 = 11:25).
    assert BollingerRsiReversion(BollingerRsiReversionParams(
        last_entry_minutes_before_close=275)).on_bar(bars, None) is None
    assert isinstance(BollingerRsiReversion(BollingerRsiReversionParams(
        last_entry_minutes_before_close=270)).on_bar(bars, None), Entry)
    # No signal on the first three bars, whatever the indicators say.
    early = two_days(FLAT, [99.0, 98.0, 97.0])
    assert BollingerRsiReversion().on_bar(early, None) is None


def test_midday_window_blocks_the_entry_but_not_the_exit():
    # Signal bar 11:25 fills at 11:30: inside the window. A signal at 13:55 fills at 14:00: allowed.
    assert BollingerRsiReversion(MIDDAY).on_bar(drop(23), None) is None
    assert isinstance(BollingerRsiReversion(MIDDAY).on_bar(drop(53), None), Entry)  # starts 13:55
    exit_bars = two_days(FLAT, [100.0] * 23 + [99.0, 100.0])  # 11:30 bar: fill at 11:35
    assert BollingerRsiReversion(MIDDAY).on_bar(exit_bars, held()) == Exit("middle_band")


def test_time_exit_on_the_bar_closing_at_1555_and_on_the_half_day_at_1255():
    s = BollingerRsiReversion()
    quiet = [100.0] * 77  # no cross of the middle band, so only the clock can exit
    assert s.on_bar(two_days(FLAT, quiet[:76]), held()) in (None, Exit("middle_band"))
    assert s.on_bar(two_days(FLAT, quiet), held()) == Exit("time")  # starts 15:50
    days = (HALF_DAY1, HALF_DAY2)
    assert s.on_bar(two_days(FLAT, quiet[:41], days=days), held()) == Exit("time")  # 12:50


def test_no_entry_while_in_a_position():
    assert BollingerRsiReversion().on_bar(drop(), held()) is None


def test_defaults_match_spec():
    p = BollingerRsiReversionParams()
    assert (p.bb_period, p.bb_k, p.rsi_period, p.rsi_max) == (20, 2.0, 2, 10.0)
    assert (p.atr_period, p.atr_multiple) == (14, 2.0)
    assert (p.first_signal, p.last_entry_minutes_before_close) == (time(9, 45), 30)
    assert (p.flatten_minutes_before_close, p.no_entry_from, p.no_entry_until) == (5, None, None)


def test_engine_round_trip_enters_on_the_drop_and_exits_at_the_middle_band():
    today = [100.0] * 23 + [99.0] + [99.0] * 3 + [100.0] + [100.0] * 50
    result = run_backtest(two_days(FLAT, today), BollingerRsiReversion())
    t = result.trades
    assert list(t["reason"]) == ["middle_band"]
    assert pd.Timestamp(t["exit_time"].iloc[0]).tz_convert(TZ).strftime("%H:%M") < "12:00"
