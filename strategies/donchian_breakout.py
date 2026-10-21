"""donchian_breakout, written from strategies/specs/donchian_breakout.yaml (version 1).

Buy when the close breaks above the highest high of the previous 20 bars (a Donchian
channel is the highest high and lowest low of the last N bars; breaking out of the top
says price is trending up). Stop: the 20-bar low, or 2 ATR (average bar size) below the
entry, whichever is closer. No profit target: ride the trend until the stop or the
flatten before the close. Paper trading / education only. Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import SessionClock, entry_blocked


@dataclass(frozen=True)
class DonchianBreakoutParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    channel_bars: int = 20
    atr_period: int = 14
    atr_multiple: float = 2.0
    first_signal: time = time(9, 45)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    no_entry_from: time | None = None  # e.g. time(11, 30): midday filter, off by default
    no_entry_until: time | None = None  # e.g. time(14, 0)
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE


class DonchianBreakout(Strategy):
    def __init__(self, params: DonchianBreakoutParams | None = None):
        self.p = params or DonchianBreakoutParams()
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

        if position is not None:
            return Exit("time") if flatten_now else None

        if not may_enter or start.time() < p.first_signal:
            return None
        if entry_blocked(start, p.no_entry_from, p.no_entry_until):
            return None
        n = p.channel_bars
        if len(bars) <= max(n, p.atr_period):
            return None

        close, high, low = bars["close"], bars["high"], bars["low"]
        upper = high.iloc[-n - 1 : -1].max()  # previous n bars: the signal bar is not in it
        entry = float(close.iloc[-1])
        if not entry > upper:
            return None

        atr = indicators.atr(high, low, close, p.atr_period).iloc[-1]
        if pd.isna(atr):
            return None
        stop = max(float(low.iloc[-n:].min()), entry - p.atr_multiple * float(atr))
        if stop >= entry:
            return None
        return Entry(stop=stop, target=float("inf"), reason="donchian_breakout")
