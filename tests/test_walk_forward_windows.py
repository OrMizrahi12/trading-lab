"""make_windows must follow the data: every window needs bars to trade, whether the cache holds 24 or 18 months."""

import pandas as pd
import pytest

from backtests.walk_forward import cut, make_windows


def weekday_bars(first: str, last: str) -> pd.DataFrame:
    days = pd.bdate_range(first, last)
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=14, minutes=30) for d in days], tz="UTC")
    return pd.DataFrame({"close": 1.0}, index=idx)


@pytest.mark.parametrize("last,expected", [("2026-10-21", 9),   # the full 24 months
                                           ("2026-04-21", 6)])  # last six months held out
def test_every_window_has_bars_to_trade(last, expected):
    bars = weekday_bars("2024-10-21", last)
    windows = make_windows(bars)
    assert len(windows) == expected
    assert all(len(cut(bars, w["trade"])) > 0 for w in windows)
    assert len(cut(bars, windows[-1]["trade"])) > 0 and windows[-1]["trade"][1] == pd.Timestamp.max


def test_traded_months_do_not_overlap_or_leave_gaps():
    windows = make_windows(weekday_bars("2024-10-21", "2026-04-21"))
    for a, b in zip(windows, windows[1:]):
        assert a["trade"][1] == b["trade"][0]
