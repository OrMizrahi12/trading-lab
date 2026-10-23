import datetime as dt

import pandas as pd
import pytest

from reports.gap_chart import build_figure, fetch_bars, split_session


def bars():
    # 09:30 and 15:55 New York on Oct 21, 09:30 and 09:35 on Oct 22 2026 (EDT = UTC-4), as UTC like IB sends.
    rows = [("2026-10-21 13:30", 90, 91, 89, 90), ("2026-10-21 19:55", 90, 90, 89.5, 89.85),
            ("2026-10-22 13:30", 78.37, 79, 77, 78), ("2026-10-22 13:35", 78, 80, 78, 79)]
    return pd.DataFrame([{"date": pd.Timestamp(t, tz="UTC"), "open": o, "high": h, "low": l, "close": c}
                         for t, o, h, l, c in rows])


def test_split_session_takes_last_close_of_earlier_day():
    today, prev_close = split_session(bars(), dt.date(2026, 10, 22))
    assert len(today) == 2 and prev_close == 89.85


def test_split_session_errors():
    with pytest.raises(ValueError, match="no bars"):
        split_session(bars(), dt.date(2026, 10, 24))
    with pytest.raises(ValueError, match="no earlier"):
        split_session(bars(), dt.date(2026, 10, 21))


def test_figure_has_dashed_line_and_neutral_title():
    day = dt.date(2026, 10, 22)
    today, prev_close = split_session(bars(), day)
    fig = build_figure("HXL", day, today, prev_close)
    line = fig.layout.shapes[0]
    assert line.y0 == 89.85 and line.line.dash == "dash"
    assert "HXL" in fig.layout.title.text


def test_fetch_refuses_non_paper_port():
    with pytest.raises(RuntimeError, match="not a paper port"):
        fetch_bars("HXL", dt.date(2026, 10, 22), 9999)
