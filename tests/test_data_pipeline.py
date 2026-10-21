"""Offline tests for data_pipeline: a fake IB serves canned bars, no Gateway needed."""

from datetime import datetime, time, timedelta, timezone

import pandas as pd
import pytest
from ib_async import BarData, Event

from data_pipeline import cache
from data_pipeline.config import BAR_SIZES
from data_pipeline.downloader import PacingError, download_missing
from market_data import Throttle

PACING = (162, "Historical Market Data Service error message: Historical data request pacing violation")
UTC = timezone.utc


def session_bars(step_minutes, first_day, last_day):
    """Regular-hours bars (13:30-20:00 UTC) on weekdays, as IB would report them."""
    out, day = [], first_day
    while day <= last_day:
        if day.weekday() < 5:
            t = datetime.combine(day, time(13, 30), UTC)
            while t < datetime.combine(day, time(20, 0), UTC):
                out.append(t)
                t += timedelta(minutes=step_minutes)
        day += timedelta(days=1)
    return out


class FakeIB:
    def __init__(self, bar_times, errors=(), fail_after=None, timeouts=0, clock=None):
        self.bar_times, self.errors, self.fail_after = bar_times, list(errors), fail_after
        self.timeouts, self.clock = timeouts, clock  # a timeout = empty reply, no error, 60 s later
        self.errorEvent = Event()
        self.requests, self.sleeps = [], []

    def qualifyContracts(self, contract):
        contract.conId = 1

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def reqHistoricalData(self, contract, endDateTime, durationStr, **kwargs):
        assert endDateTime.tzinfo is not None
        self.requests.append(endDateTime)
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            self.errorEvent.emit(1, 354, "Requested market data is not subscribed")
            return []
        if self.timeouts > 0:
            self.timeouts -= 1
            self.clock[0] += 60
            return []
        if self.errors:
            code, text = self.errors.pop(0)
            self.errorEvent.emit(1, code, text)
            return []
        span = timedelta(days={"1 D": 1, "1 W": 7}[durationStr])
        return [
            BarData(date=t, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0, average=1.2, barCount=3)
            for t in self.bar_times
            if endDateTime - span <= t < endDateTime
        ]


@pytest.fixture
def throttle():
    return Throttle(max_calls=10_000, same_gap=0)


def run(ib, tmp_path, throttle, now, bar_size="5min"):
    return download_missing(ib, "AAPL", bar_size, cache_dir=tmp_path, now=now, throttle=throttle)


def test_first_run_seeds_utc_sorted_no_forming_bar(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)  # Wednesday, session in progress
    ib = FakeIB(session_bars(5, datetime(2026, 8, 1).date(), now.date()))
    run(ib, tmp_path, throttle, now)

    df = cache.read(cache.cache_path("AAPL", "5min", tmp_path))
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert df.index.min() >= now - timedelta(days=30)
    assert df.index.max() == pd.Timestamp("2026-10-21 17:55", tz="UTC")  # 18:00 bar is still forming


def test_second_run_fetches_only_newer_bars(tmp_path, throttle):
    times = session_bars(5, datetime(2026, 8, 1).date(), datetime(2026, 10, 22).date())
    run(FakeIB(times), tmp_path, throttle, datetime(2026, 10, 21, 18, 3, tzinfo=UTC))
    first = cache.read(cache.cache_path("AAPL", "5min", tmp_path))

    ib = FakeIB(times)
    result = run(ib, tmp_path, throttle, datetime(2026, 10, 21, 19, 3, tzinfo=UTC))
    second = cache.read(cache.cache_path("AAPL", "5min", tmp_path))

    assert len(ib.requests) == 1  # nothing older than the cache was requested
    assert result["added"] == len(second) - len(first) == 12
    assert second.index.is_unique and second.index.max() == pd.Timestamp("2026-10-21 18:55", tz="UTC")


def test_pacing_error_is_retried_with_backoff(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    ib = FakeIB(session_bars(5, datetime(2026, 8, 1).date(), now.date()), errors=[PACING, PACING])
    run(ib, tmp_path, throttle, now)
    assert [s for s in ib.sleeps if s >= 15] == [15, 30]
    assert cache.cache_path("AAPL", "5min", tmp_path).exists()


def test_persistent_pacing_error_raises_and_writes_nothing(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    ib = FakeIB([], errors=[PACING] * 10)
    with pytest.raises(PacingError):
        run(ib, tmp_path, throttle, now)
    assert not cache.cache_path("AAPL", "5min", tmp_path).exists()


def test_failure_midway_leaves_cache_untouched(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    ib = FakeIB(session_bars(5, datetime(2026, 8, 1).date(), now.date()), fail_after=2)
    with pytest.raises(RuntimeError, match="354"):
        run(ib, tmp_path, throttle, now)
    assert not cache.cache_path("AAPL", "5min", tmp_path).exists()


def test_trading_day_seed_skips_weekend_without_retries(tmp_path, throttle):
    now = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)  # Monday: Mon + Fri + Thu are the 3 sessions
    ib = FakeIB(session_bars(1, datetime(2026, 10, 1).date(), now.date()))
    run(ib, tmp_path, throttle, now, "1min")

    df = cache.read(cache.cache_path("AAPL", "1min", tmp_path))
    days = sorted({d.date().isoformat() for d in df.index})
    assert days == ["2026-10-15", "2026-10-16", "2026-10-19"]
    assert not [s for s in ib.sleeps if s >= 15]  # empty weekend chunks are not pacing errors
    assert len(ib.requests) <= 6


def test_trading_day_seed_has_no_partial_oldest_session(tmp_path, throttle):
    now = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)  # Wednesday, 30 minutes after the open
    ib = FakeIB(session_bars(1, datetime(2026, 10, 1).date(), now.date()))
    run(ib, tmp_path, throttle, now, "1min")

    df = cache.read(cache.cache_path("AAPL", "1min", tmp_path))
    firsts = df.groupby(df.index.date).apply(lambda g: g.index.min().time())
    assert list(firsts.index.astype(str)) == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert set(firsts) == {time(13, 30)}  # every kept session starts at the open


def test_timed_out_request_is_retried_not_cached_as_empty(tmp_path, throttle, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("data_pipeline.downloader.time.monotonic", lambda: clock[0])
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    ib = FakeIB(session_bars(5, datetime(2026, 8, 1).date(), now.date()), timeouts=1, clock=clock)
    run(ib, tmp_path, throttle, now)
    assert 15 in ib.sleeps  # backed off and asked again


def test_persistent_timeout_raises_and_writes_nothing(tmp_path, throttle, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("data_pipeline.downloader.time.monotonic", lambda: clock[0])
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    ib = FakeIB([], timeouts=10, clock=clock)
    with pytest.raises(RuntimeError, match="timed out"):
        run(ib, tmp_path, throttle, now)
    assert not cache.cache_path("AAPL", "5min", tmp_path).exists()


def test_data_farm_drop_is_retried(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    farm_down = (2105, "HMDS data farm connection is broken: ushmds")
    ib = FakeIB(session_bars(5, datetime(2026, 8, 1).date(), now.date()), errors=[farm_down])
    run(ib, tmp_path, throttle, now)
    assert 15 in ib.sleeps


def test_no_bars_at_all_reports_empty_cache(tmp_path, throttle):
    now = datetime(2026, 10, 21, 18, 3, tzinfo=UTC)
    result = run(FakeIB([]), tmp_path, throttle, now)
    assert result["added"] == 0 and result["first"] is None and result["last"] is None


def test_cache_rejects_naive_timestamps(tmp_path):
    df = pd.DataFrame({c: [1.0] for c in cache.COLUMNS}, index=pd.DatetimeIndex(["2026-10-21 14:00"], name="date"))
    with pytest.raises(ValueError):
        cache.merge_and_write(tmp_path / "x.parquet", df)


def test_bar_sizes_cover_chunks():
    assert BAR_SIZES["5min"].chunk_duration == "1 W" and BAR_SIZES["1min"].chunk_duration == "1 D"
