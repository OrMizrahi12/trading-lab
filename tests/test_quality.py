"""Offline tests for data_pipeline.quality: hand-built bars with one known defect each."""

import pandas as pd

from data_pipeline import cache, quality

NOW = pd.Timestamp("2026-10-30 12:00", tz="UTC")  # after every session used below


def good_day(day, step=5, open_utc="13:30", close_utc="20:00"):
    """Clean bars for one day. Defaults are summer time (EDT): 09:30-16:00 New York = 13:30-20:00 UTC."""
    idx = pd.date_range(f"{day} {open_utc}", f"{day} {close_utc}", freq=f"{step}min", inclusive="left", tz="UTC")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0, "average": 10.0, "barCount": 5},
        index=idx.rename("date"),
    )


def check(tmp_path, df, name="TST_5min.parquet"):
    path = tmp_path / name
    df[cache.COLUMNS].to_parquet(path)
    return quality.check_file(path, now=NOW)


def test_clean_data_has_no_problems(tmp_path):
    df = pd.concat([good_day("2026-10-20"), good_day("2026-10-21")])
    assert check(tmp_path, df).problems == 0


def test_gap_inside_session_is_found_with_size(tmp_path):
    day = good_day("2026-10-21")
    rep = check(tmp_path, day.drop(day.index[10:13]))
    assert [(s.strftime("%H:%M"), n) for s, _, n in rep.gaps] == [("14:20", 3)]


def test_duplicate_timestamps(tmp_path):
    day = good_day("2026-10-21")
    assert check(tmp_path, pd.concat([day, day.iloc[[3]]])).duplicates == 1


def test_bad_prices_and_volume(tmp_path):
    day = good_day("2026-10-21")
    day.iloc[0, day.columns.get_loc("low")] = 0.0  # zero price
    day.iloc[1, day.columns.get_loc("high")] = 8.0  # high below low (and below close)
    day.iloc[2, day.columns.get_loc("close")] = 12.0  # close above high
    day.iloc[3, day.columns.get_loc("volume")] = 0.0
    rep = check(tmp_path, day)
    assert (rep.non_positive_prices, rep.high_below_low, rep.close_outside_range, rep.zero_volume) == (1, 1, 1, 1)


def test_missing_whole_day_and_holiday_is_not_missing(tmp_path):
    # Thu 2026-11-26 is Thanksgiving: NYSE closed, so no bars there is correct.
    # Winter time (EST) is UTC-5: open 14:30 UTC. The 27th is a half day closing 13:00 NY = 18:00 UTC.
    df = pd.concat([
        good_day("2026-11-24", open_utc="14:30", close_utc="21:00"),
        good_day("2026-11-27", open_utc="14:30", close_utc="18:00"),
    ])
    rep = quality.check_file(_write(tmp_path, df), now=pd.Timestamp("2026-12-01", tz="UTC"))
    assert [d.isoformat() for d in rep.missing_days] == ["2026-11-25"]  # Wed is a real hole, Thu is not
    assert rep.gaps == []  # the half day is judged against its 13:00 close, not 16:00


def test_bars_on_a_holiday_are_flagged(tmp_path):
    df = pd.concat([good_day("2026-11-25"), good_day("2026-11-26")])
    assert check(tmp_path, df).extra_days[0].isoformat() == "2026-11-26"


def test_session_still_running_is_not_a_gap(tmp_path):
    day = good_day("2026-10-21").iloc[:30]  # data until 15:55 UTC
    path = _write(tmp_path, day)
    rep = quality.check_file(path, now=pd.Timestamp("2026-10-21 16:00", tz="UTC"))
    assert rep.gaps == []


def _write(tmp_path, df):
    path = tmp_path / "TST_5min.parquet"
    df[cache.COLUMNS].to_parquet(path)
    return path
