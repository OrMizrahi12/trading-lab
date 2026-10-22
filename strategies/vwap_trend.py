"""vwap_trend, written from strategies/specs/vwap_trend.yaml (version 1).

Be long while the price is above VWAP (the session's volume-weighted average price, the
average price paid today). Enter on the bar whose close crosses from at-or-below VWAP to
above it, exit on a bar that closes below VWAP. Stop: atr_multiple x ATR (average bar
size) below the entry. No target. Flatten before the close. Long only: the paper's short
side needs engine work. Paper trading / education only. Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import SessionClock, entry_blocked


@dataclass(frozen=True)
class VwapTrendParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    atr_period: int = 14
    atr_multiple: float = 2.0
    first_signal: time = time(9, 45)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    no_entry_from: time | None = None
    no_entry_until: time | None = None
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"


class VwapTrend(Strategy):
    def __init__(self, params: VwapTrendParams | None = None):
        self.p = params or VwapTrendParams()
        self._clock = SessionClock(self.p.calendar)

    def on_bar(self, bars: pd.DataFrame, position: Position | None) -> Entry | Exit | None:
        p = self.p
        start = bars.index[-1].tz_convert(p.timezone)
        phase = self._clock.phase(
            start, p.last_entry_minutes_before_close, p.flatten_minutes_before_close
        )
        if phase is None:
            return Exit("time") if position is not None else None
        flatten_now, may_enter = phase

        if position is not None and flatten_now:
            return Exit("time")
        if position is None and (
            not may_enter
            or start.time() < p.first_signal
            or entry_blocked(start, p.no_entry_from, p.no_entry_until)
        ):
            return None
        if len(bars) < 2:
            return None

        close, high, low = bars["close"], bars["high"], bars["low"]
        vwap = indicators.vwap(high, low, close, bars["volume"])
        above = close > vwap  # False wherever an input is NaN

        if position is not None:
            return Exit("below_vwap") if close.iloc[-1] < vwap.iloc[-1] else None

        if not above.iloc[-1] or above.iloc[-2]:  # only on the bar price crosses above
            return None
        atr = indicators.atr(high, low, close, p.atr_period).iloc[-1]
        if pd.isna(atr):
            return None
        entry = float(close.iloc[-1])
        stop = entry - p.atr_multiple * float(atr)
        if stop >= entry:
            return None
        return Entry(stop=stop, target=float("inf"), reason="vwap_trend")
