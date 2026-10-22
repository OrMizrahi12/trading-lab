"""vwap_stretch_reversion, written from strategies/specs/vwap_stretch_reversion.yaml (version 1).

Mean reversion (the bet that a stretched price snaps back toward its average). Buy when the
close is more than 2 ATR (average bar size) below VWAP (the day's volume-weighted average
price): the price has been pushed unusually far under what the day's average buyer paid.
Exit when a bar closes back at or above VWAP. Stop: 2 ATR below the entry. Flatten before
the close. Paper trading / education only. Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import SessionClock, entry_blocked, trend_day_blocked


@dataclass(frozen=True)
class VwapStretchReversionParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    stretch_atr_multiple: float = 2.0  # how far under VWAP, in ATRs, counts as stretched
    atr_period: int = 14
    atr_multiple: float = 2.0  # the stop distance below the entry, in ATRs
    first_signal: time = time(9, 45)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    no_entry_from: time | None = None  # e.g. time(11, 30): midday filter, off by default
    no_entry_until: time | None = None  # e.g. time(14, 0)
    max_open_distance_atr: float | None = None  # trend-day filter: no entry once the close is further than this many ATRs from today's open; off by default
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE


class VwapStretchReversion(Strategy):
    def __init__(self, params: VwapStretchReversionParams | None = None):
        self.p = params or VwapStretchReversionParams()
        self._clock = SessionClock(self.p.calendar)

    def on_bar(self, bars: pd.DataFrame, position: Position | None) -> Entry | Exit | None:
        p = self.p
        start = bars.index[-1].tz_convert(p.timezone)
        phase = self._clock.phase(
            start, p.last_entry_minutes_before_close, p.flatten_minutes_before_close
        )
        if phase is None:  # data on a day the exchange was shut: do nothing
            return Exit("time") if position is not None else None
        flatten_now, may_enter = phase

        if position is not None and flatten_now:
            return Exit("time")
        if position is None and (
            not may_enter
            or start.time() < p.first_signal
            or entry_blocked(start, p.no_entry_from, p.no_entry_until)
            or trend_day_blocked(bars, p.timezone, p.max_open_distance_atr, p.atr_period)
        ):
            return None
        if len(bars) <= p.atr_period:
            return None

        close, high, low = bars["close"], bars["high"], bars["low"]
        vwap = float(indicators.vwap(high, low, close, bars["volume"]).iloc[-1])
        last = float(close.iloc[-1])

        if position is not None:  # NaN compares False, so no exit on an undefined VWAP
            return Exit("vwap") if last >= vwap else None

        atr = indicators.atr(high, low, close, p.atr_period).iloc[-1]
        if pd.isna(atr) or not last < vwap - p.stretch_atr_multiple * float(atr):
            return None
        stop = last - p.atr_multiple * float(atr)
        if stop >= last:
            return None
        return Entry(stop=stop, target=float("inf"), reason="vwap_stretch_reversion")
