"""Offline tests for the trend-day filter (max_open_distance_atr) on both reversion strategies.

Yesterday is 30 flat bars at 100; today opens at 100, sits flat for 23 bars and then closes at
`close` (signal bar 11:25). The filter measures that close's distance from today's open in ATRs.
"""

import pandas as pd
import pytest

import indicators
from helpers import TZ, day_bars, two_days
from strategies.base import Entry
from strategies.bollinger_rsi_reversion import BollingerRsiReversion, BollingerRsiReversionParams
from strategies.session_clock import open_distance_atr, trend_day_blocked
from strategies.vwap_stretch_reversion import VwapStretchReversion, VwapStretchReversionParams

FLAT = [100.0] * 30


def drop(close: float = 98.4) -> pd.DataFrame:
    return two_days(FLAT, [100.0] * 23 + [close])


def test_distance_is_close_minus_todays_open_in_atrs_and_ignores_direction():
    for close in (98.4, 101.6):
        bars = drop(close)
        atr = indicators.atr(bars.high, bars.low, bars.close, 14).iloc[-1]
        assert open_distance_atr(bars, TZ) == pytest.approx(1.6 / atr)


def test_distance_is_measured_from_todays_open_not_yesterdays_close():
    flat_day = day_bars("2026-03-02", FLAT, 100.0)
    today = day_bars("2026-03-03", [95.0] * 24, 95.0)  # gapped down 5 at the open, then flat
    bars = pd.concat([flat_day, today])
    assert open_distance_atr(bars, TZ) == pytest.approx(0.0)  # the gap does not count as a move since the open


def test_distance_uses_only_the_bars_given_no_look_ahead():
    bars = drop()
    assert open_distance_atr(bars.iloc[:-1], TZ) == pytest.approx(0.0)  # before the drop closed


def test_distance_is_none_while_the_atr_is_undefined():
    assert open_distance_atr(two_days([100.0] * 3, [100.0] * 3), TZ) is None


def test_filter_off_never_blocks_and_unknown_distance_blocks_when_on():
    assert not trend_day_blocked(two_days([100.0] * 3, [100.0] * 3), TZ, None)
    assert trend_day_blocked(two_days([100.0] * 3, [100.0] * 3), TZ, 5.0)


@pytest.mark.parametrize("cls, params", [
    (BollingerRsiReversion, BollingerRsiReversionParams),
    (VwapStretchReversion, VwapStretchReversionParams),
])
def test_entry_blocked_when_price_is_further_from_the_open_than_the_limit(cls, params):
    bars = drop()
    dist = open_distance_atr(bars, TZ)
    assert isinstance(cls(params()).on_bar(bars, None), Entry)  # filter off: entry
    assert isinstance(cls(params(max_open_distance_atr=dist + 0.5)).on_bar(bars, None), Entry)  # inside the limit
    assert cls(params(max_open_distance_atr=dist - 0.5)).on_bar(bars, None) is None  # trend day: stand aside


def test_the_filter_never_blocks_an_exit():
    from strategies.base import Exit, Position
    bars = two_days(FLAT, [100.0] * 23 + [98.4, 100.0])
    held = Position(pd.Timestamp("2026-03-03 15:00", tz="UTC"), 98.4, 90.0, float("inf"))
    s = BollingerRsiReversion(BollingerRsiReversionParams(max_open_distance_atr=0.0))
    assert s.on_bar(bars, held) == Exit("middle_band")


def test_default_filter_is_off():
    assert BollingerRsiReversionParams().max_open_distance_atr is None
    assert VwapStretchReversionParams().max_open_distance_atr is None
