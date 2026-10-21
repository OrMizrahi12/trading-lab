"""ema_trend, written from strategies/specs/ema_trend.yaml (version 1).

Be long while the 20-bar EMA (exponential moving average, a fast-reacting average) is
above the 50-bar EMA and the price is above VWAP (the day's volume-weighted average
price): a short-term uptrend that the day's buyers are also backing. Enter on the bar
where both become true, exit when either stops being true. Stop: 2 ATR (average bar
size) below the entry. No profit target. Flatten before the close. Paper trading /
education only. Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import SessionClock, entry_blocked


@dataclass(frozen=True)
class EmaTrendParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    fast_period: int = 20
    slow_period: int = 50
    atr_period: int = 14
    atr_multiple: float = 2.0
    first_signal: time = time(9, 45)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    no_entry_from: time | None = None  # e.g. time(11, 30): midday filter, off by default
    no_entry_until: time | None = None  # e.g. time(14, 0)
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE


class EmaTrend(Strategy):
    def __init__(self, params: EmaTrendParams | None = None):
        self.p = params or EmaTrendParams()
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
        ):
            return None
        # The slow EMA needs slow_period bars, and one more for the "was false" check.
        if len(bars) <= max(p.slow_period, p.atr_period):
            return None

        close, high, low = bars["close"], bars["high"], bars["low"]
        fast = indicators.ema(close, p.fast_period)
        slow = indicators.ema(close, p.slow_period)
        vwap = indicators.vwap(high, low, close, bars["volume"])
        up = (fast > slow) & (close > vwap)  # False wherever an input is NaN

        if position is not None:
            return Exit("trend_flip") if not up.iloc[-1] else None

        if not up.iloc[-1] or up.iloc[-2]:  # enter only on the bar the trend switches on
            return None
        atr = indicators.atr(high, low, close, p.atr_period).iloc[-1]
        if pd.isna(atr):
            return None
        entry = float(close.iloc[-1])
        stop = entry - p.atr_multiple * float(atr)
        if stop >= entry:
            return None
        return Entry(stop=stop, target=float("inf"), reason="ema_trend")
