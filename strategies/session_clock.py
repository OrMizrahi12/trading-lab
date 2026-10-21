"""Time-of-day rules shared by the intraday strategies.

Last-entry and flatten times are offsets from each day's real exchange close (13:00 on
an NYSE half day, not a fixed 16:00). The decision is made at a bar's close and filled
at the next open, so "flatten 5 minutes before the close" means deciding on the bar
that closes then. Paper trading / education only. Nothing here is financial advice.
"""

from datetime import time

import exchange_calendars as xcals
import pandas as pd

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
