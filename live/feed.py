"""Closed 5-minute bars from polled IB history, with the delay of every bar logged. Paper trading only.

The account has no real-time data, so there is no stream: once a minute we ask for the last
week of 5-minute bars and keep only the CLOSED ones (bars are stamped with their start, so a
bar is closed when start + 5 minutes <= now). Delayed data means a bar closes long before we
get to see it; the log file records how long.
"""

import csv
import logging
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from data_pipeline.downloader import fetch_chunk
from market_data import hist_throttle

from .config import BAR_MINUTES, LOOKBACK_BARS, MAX_BAR_DELAY_MIN, SETTLE_MIN

log = logging.getLogger("live")

BAR = pd.Timedelta(minutes=BAR_MINUTES)
NY = "America/New_York"
DELAY_FIELDS = ["poll_time", "bar_start", "bar_close", "delay_seconds"]


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def closed_bars(raw: pd.DataFrame, now: pd.Timestamp, settle_min: float = SETTLE_MIN) -> pd.DataFrame:
    """Sorted, de-duplicated bars that are final at `now`: their 5 minutes ended at least `settle_min` ago.

    With delayed data a bar the wall clock calls over can still be filling at IB (partial volume and prices), so the
    forming bar and the not yet settled newest bar are both dropped. `settle_min=0` is the plain wall-clock rule.
    """
    if raw.empty:
        return raw
    bars = raw[~raw.index.duplicated(keep="last")].sort_index()
    return bars[bars.index + BAR + pd.Timedelta(minutes=settle_min) <= now]


def find_holes(closed: pd.DataFrame, lookback: int = LOOKBACK_BARS, calendar=None) -> list[pd.Timestamp]:
    """Bar starts the NYSE calendar expects inside the last `lookback` bars that IB did not send."""
    if closed.empty:
        return []
    cal = calendar or xcals.get_calendar("XNYS")
    tail = closed.index[-lookback:]
    first, last = tail[0], tail[-1]
    days = pd.Timestamp(first.tz_convert(NY).date()), pd.Timestamp(last.tz_convert(NY).date())
    holes: list[pd.Timestamp] = []
    for session in cal.sessions_in_range(*days):
        expected = pd.date_range(cal.session_open(session), cal.session_close(session), freq=BAR, inclusive="left")
        expected = expected[(expected >= first) & (expected <= last)]
        holes += list(expected.difference(tail))
    return holes


class BarFeed:
    def __init__(self, ib, contract, *, delay_log: Path | None = None, clock=utc_now, throttle=hist_throttle,
                 max_delay_min: int = MAX_BAR_DELAY_MIN, fetch=fetch_chunk, lookback: int = LOOKBACK_BARS,
                 settle_min: float = SETTLE_MIN):
        self.settle_min = settle_min
        self.ib, self.contract = ib, contract
        self.delay_log, self.clock, self.throttle, self.fetch = delay_log, clock, throttle, fetch
        self.max_delay = pd.Timedelta(minutes=max_delay_min)
        self.lookback = lookback
        self.holes: list[pd.Timestamp] = []  # missing bars inside the lookback window after the last poll
        self._logged_upto: pd.Timestamp | None = None

    def poll(self) -> pd.DataFrame:
        """One request. Returns all closed bars IB sent (about a week), oldest first."""
        now = self.clock()
        raw = self.fetch(self.ib, self.contract, now, "5min", self.throttle)
        closed = closed_bars(raw, now, self.settle_min)
        self._log_delays(closed, now)
        self.holes = find_holes(closed, self.lookback)
        if self.holes:
            log.warning("feed: %d missing bar(s) in the last %d, first %s", len(self.holes), self.lookback, self.holes[0])
        return closed

    def _log_delays(self, closed: pd.DataFrame, now: pd.Timestamp) -> None:
        """Delay = poll time minus the bar's close, measured when we first see the bar (one poll of accuracy).

        The first poll of a run only logs the newest bar: older ones were seen long ago, so their delay is unknown.
        """
        if closed.empty:
            return
        new = closed.index[-1:] if self._logged_upto is None else closed.index[closed.index > self._logged_upto]
        if len(new) == 0:
            return
        self._logged_upto = new[-1]
        rows = []
        for start in new:
            delay = now - (start + BAR)
            rows.append({"poll_time": now.isoformat(), "bar_start": start.isoformat(),
                         "bar_close": (start + BAR).isoformat(), "delay_seconds": int(delay.total_seconds())})
            if delay > self.max_delay:
                log.warning("feed: bar %s is %s old when first seen (limit %s)", start, delay, self.max_delay)
        log.info("feed: newest closed bar %s, delay %ss", new[-1], rows[-1]["delay_seconds"])
        if self.delay_log is None:
            return
        self.delay_log.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.delay_log.exists()
        with self.delay_log.open("a", newline="") as f:
            writer = csv.DictWriter(f, DELAY_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)
