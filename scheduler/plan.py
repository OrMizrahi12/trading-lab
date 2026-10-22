"""The day's timetable, from the NYSE calendar. Paper trading only.

Prep and the runner start are New York wall-clock times (09:00, 09:25); the flatten and the stop are offsets from the
real close (close - 5 min, close + 5 min), so a half day (13:00 close) ends early without a special case. Every time is
built in New York time and converted to UTC, so daylight saving never shifts anything.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.session_clock import SessionClock

NY = ZoneInfo("America/New_York")
PREP_AT = time(9, 0)
RUNNER_AT = time(9, 25)
FLATTEN_BEFORE_CLOSE_MIN = 5
STOP_AFTER_CLOSE_MIN = 5
FULL_DAY_CLOSE = time(16, 0)


@dataclass(frozen=True)
class DayPlan:
    session: date
    close: pd.Timestamp  # UTC
    prep: pd.Timestamp  # all UTC
    runner: pd.Timestamp
    flatten: pd.Timestamp
    stop: pd.Timestamp

    @property
    def half_day(self) -> bool:
        return self.close.tz_convert(NY).time() < FULL_DAY_CLOSE


def _ny(day: date, at: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, at, tzinfo=NY)).tz_convert("UTC")


def plan_for(day: date, clock: SessionClock | None = None) -> DayPlan | None:
    """The timetable for `day`, or None if the NYSE is shut (weekend or holiday)."""
    close = (clock or SessionClock()).close(day)
    if close is None:
        return None
    return DayPlan(session=day, close=close, prep=_ny(day, PREP_AT), runner=_ny(day, RUNNER_AT),
                   flatten=close - pd.Timedelta(minutes=FLATTEN_BEFORE_CLOSE_MIN),
                   stop=close + pd.Timedelta(minutes=STOP_AFTER_CLOSE_MIN))


def next_plans(start: date, n: int, clock: SessionClock | None = None) -> list[DayPlan]:
    """The next `n` trading days from `start` (inclusive)."""
    clock = clock or SessionClock()
    out, day = [], start
    while len(out) < n:
        plan = plan_for(day, clock)
        if plan:
            out.append(plan)
        day += timedelta(days=1)
    return out


def next_open_plan(now: pd.Timestamp, clock: SessionClock | None = None) -> DayPlan:
    """The first trading day whose stop time is still ahead: today while it is running, else the next one."""
    clock = clock or SessionClock()
    day = now.tz_convert(NY).date()
    while True:
        plan = plan_for(day, clock)
        if plan and plan.stop > now:
            return plan
        day += timedelta(days=1)
