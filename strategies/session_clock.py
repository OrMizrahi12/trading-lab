"""Time-of-day rules shared by the intraday strategies.

Last-entry and flatten times are offsets from each day's real exchange close (13:00 on
an NYSE half day, not a fixed 16:00). The decision is made at a bar's close and filled
at the next open, so "flatten 5 minutes before the close" means deciding on the bar
that closes then. Paper trading / education only. Nothing here is financial advice.
"""

from datetime import time

import exchange_calendars as xcals
import pandas as pd

import indicators

BAR_MINUTES = 5


class SessionClock:
    def __init__(self, calendar: str = "XNYS"):  # XNYS = New York Stock Exchange
        self._cal = xcals.get_calendar(calendar)
        self._closes: dict = {}  # session date -> real close (UTC), or None if not a session

    def close(self, day) -> pd.Timestamp | None:
        if day not in self._closes:
            try:
                self._closes[day] = self._cal.session_close(pd.Timestamp(day))
            except xcals.errors.NotSessionError:
                self._closes[day] = None
        return self._closes[day]

    def phase(
        self, start: pd.Timestamp, last_entry_minutes: int, flatten_minutes: int
    ) -> tuple[bool, bool] | None:
        """For the bar starting at `start` (local time): (flatten_now, may_enter).

        None means the exchange was shut that day (data on a non-session date).
        """
        session_close = self.close(start.date())
        if session_close is None:
            return None
        bar_end = start + pd.Timedelta(minutes=BAR_MINUTES)
        flatten_now = bar_end >= session_close - pd.Timedelta(minutes=flatten_minutes)
        may_enter = start < session_close - pd.Timedelta(minutes=last_entry_minutes)
        return flatten_now, may_enter


def entry_blocked(start: pd.Timestamp, no_entry_from: time | None, no_entry_until: time | None) -> bool:
    """True if a buy filled at this bar's close (the next open) lands inside the no-entry window.

    The window is [from, until) on the fill time, so with 11:30 to 14:00 the last blocked
    signal bar starts 13:50 and the first allowed one starts 13:55 (fills at 14:00).
    """
    if no_entry_from is None or no_entry_until is None:
        return False
    fill = (start + pd.Timedelta(minutes=BAR_MINUTES)).time()
    return no_entry_from <= fill < no_entry_until


def open_distance_atr(bars: pd.DataFrame, timezone: str, atr_period: int = 14) -> float | None:
    """How far the last close is from today's open, in ATRs (average bar size). Always >= 0.

    Uses only closed bars, so it is known at decision time. None while the ATR is undefined.
    """
    if len(bars) <= atr_period:
        return None
    local = bars.index.tz_convert(timezone).date
    today_open = float(bars["open"][local == local[-1]].iloc[0])
    atr = indicators.atr(bars["high"], bars["low"], bars["close"], atr_period).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None
    return abs(float(bars["close"].iloc[-1]) - today_open) / float(atr)


def trend_day_blocked(
    bars: pd.DataFrame, timezone: str, max_open_distance_atr: float | None, atr_period: int = 14
) -> bool:
    """True if the trend-day filter is on and price is already further than that many ATRs from today's open.

    Unknown (ATR not defined yet) counts as blocked: no entry without the information.
    """
    if max_open_distance_atr is None:
        return False
    dist = open_distance_atr(bars, timezone, atr_period)
    return dist is None or dist > max_open_distance_atr
