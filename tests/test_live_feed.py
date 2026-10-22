"""Feed: only closed bars, delay logged per bar, holes found against the NYSE calendar."""

import logging

import pandas as pd

from live.feed import BarFeed, closed_bars, find_holes
from tests.helpers import DAY1, DAY2, HALF_DAY1, day_bars

UTC = "UTC"


def ts(text: str) -> pd.Timestamp:  # New York wall clock -> UTC
    return pd.Timestamp(text, tz="America/New_York").tz_convert(UTC)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_feed(bars, clock, tmp_path, **kw):
    state = {"bars": bars}
    feed = BarFeed(None, None, delay_log=tmp_path / "delay.csv", clock=clock,
                   fetch=lambda ib, c, end, size, thr: state["bars"], **kw)
    return feed, state


def test_forming_bar_is_dropped_and_duplicates_collapse():
    bars = day_bars(DAY1, [100 + i for i in range(6)], 100)  # 09:30 .. 09:55
    dup = pd.concat([bars, bars.iloc[[3]]])
    out = closed_bars(dup, ts(f"{DAY1} 09:58"))  # the 09:55 bar closes at 10:00: still forming
    assert list(out.index) == list(bars.index[:5])
    assert closed_bars(dup, ts(f"{DAY1} 10:00")).index[-1] == bars.index[-1]  # closed exactly at 10:00


def test_empty_raw_is_fine():
    assert closed_bars(pd.DataFrame(), ts(f"{DAY1} 10:00")).empty


def test_delay_logged_for_each_new_bar_with_first_poll_logging_only_the_newest(tmp_path):
    bars = day_bars(DAY1, [100 + i for i in range(10)], 100)  # up to 10:15 start
    clock = Clock(ts(f"{DAY1} 10:12"))  # last closed bar: start 10:05 (closed 10:10) -> 2 minutes old
    feed, state = make_feed(bars, clock, tmp_path)
    feed.poll()
    log = pd.read_csv(tmp_path / "delay.csv")
    assert list(log["delay_seconds"]) == [120] and len(log) == 1

    clock.now = ts(f"{DAY1} 10:27")  # bars starting 10:10 and 10:15 have closed meanwhile
    feed.poll()
    log = pd.read_csv(tmp_path / "delay.csv")
    assert len(log) == 3  # 10:05 (first poll), then 10:10 and 10:15... only those that exist in `bars`
    assert list(log["delay_seconds"])[0] == 120


def test_each_bar_is_logged_once(tmp_path):
    bars = day_bars(DAY1, [100, 101, 102, 103], 100)
    clock = Clock(ts(f"{DAY1} 09:50"))
    feed, _ = make_feed(bars, clock, tmp_path)
    feed.poll()
    clock.now = ts(f"{DAY1} 09:51")
    feed.poll()
    assert len(pd.read_csv(tmp_path / "delay.csv")) == 1


def test_stale_bar_warns(tmp_path, caplog):
    bars = day_bars(DAY1, [100, 101, 102, 103], 100)
    clock = Clock(ts(f"{DAY1} 10:50"))  # newest bar closed 09:50: 60 minutes ago
    feed, _ = make_feed(bars, clock, tmp_path, max_delay_min=30)
    with caplog.at_level(logging.WARNING, logger="live"):
        feed.poll()
    assert any("old when first seen" in r.message for r in caplog.records)


def test_no_holes_across_two_sessions_and_a_half_day():
    full = pd.concat([day_bars(DAY1, [100] * 78, 100), day_bars(DAY2, [100] * 78, 100)])
    assert find_holes(full) == []
    half = day_bars(HALF_DAY1, [100] * 42, 100)  # 09:30-13:00 = 42 bars
    assert find_holes(half) == []


def test_hole_is_found_and_reported_by_the_feed(tmp_path):
    bars = day_bars(DAY1, [100 + i for i in range(10)], 100)
    gapped = bars.drop(bars.index[4])
    assert find_holes(gapped) == [bars.index[4]]
    feed, _ = make_feed(gapped, Clock(ts(f"{DAY1} 11:00")), tmp_path)
    feed.poll()
    assert feed.holes == [bars.index[4]]


def test_hole_outside_the_lookback_window_is_ignored():
    bars = day_bars(DAY1, [100] * 78, 100)
    gapped = bars.drop(bars.index[2])
    assert find_holes(gapped, lookback=20) == []
