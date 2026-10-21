"""Data quality report for the Parquet bar cache: uv run -m data_pipeline --quality"""

from dataclasses import dataclass, field
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from . import cache
from .config import BAR_SIZES, CACHE_DIR, SESSION_TZ

CALENDAR = "XNYS"  # NYSE
PRICES = ["open", "high", "low", "close"]


@dataclass
class QualityReport:
    path: Path
    bars: int = 0
    first: pd.Timestamp | None = None
    last: pd.Timestamp | None = None
    duplicates: int = 0
    non_positive_prices: int = 0
    high_below_low: int = 0
    close_outside_range: int = 0
    zero_volume: int = 0
    missing_days: list = field(default_factory=list)  # NYSE sessions with no bars at all
    extra_days: list = field(default_factory=list)  # days with bars but no NYSE session
    gaps: list = field(default_factory=list)  # (start, end, missing bar count) inside sessions
    off_hours_bars: int = 0  # bars outside the session's open-close window
    problems: int = 0

    def __post_init__(self):
        self.problems = 0

    def finish(self):
        self.problems = (
            self.duplicates + self.non_positive_prices + self.high_below_low + self.close_outside_range
            + self.zero_volume + len(self.missing_days) + len(self.extra_days)
            + sum(n for *_, n in self.gaps) + self.off_hours_bars
        )
        return self


def _bar_size_from_name(path: Path) -> str:
    return path.stem.split("_", 1)[1]


def check_file(path: Path, now: pd.Timestamp | None = None) -> QualityReport:
    """Run every check on one cache file. `now` marks the still-running session as incomplete."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    df = cache.read(path)
    rep = QualityReport(path)
    if df is None or df.empty:
        return rep.finish()
    spec = BAR_SIZES[_bar_size_from_name(path)]
    rep.bars, rep.first, rep.last = len(df), df.index.min(), df.index.max()

    rep.duplicates = int(df.index.duplicated().sum())
    df = df[~df.index.duplicated(keep="last")].sort_index()  # later checks need unique times

    rep.non_positive_prices = int((df[PRICES] <= 0).any(axis=1).sum())
    rep.high_below_low = int((df["high"] < df["low"]).sum())
    # "outside the range" for a bar whose own high/low are sane; high<low bars are counted above
    inverted = df["high"] < df["low"]
    rep.close_outside_range = int((~inverted & ((df["close"] > df["high"]) | (df["close"] < df["low"]))).sum())
    rep.zero_volume = int((df["volume"] <= 0).sum())

    cal = xcals.get_calendar(CALENDAR)
    local_days = df.index.tz_convert(SESSION_TZ).normalize().tz_localize(None)
    first_day, last_day = local_days.min(), local_days.max()
    sessions = cal.sessions_in_range(first_day, last_day)
    present_days = set(local_days.unique())

    rep.missing_days = [d.date() for d in sessions if d not in present_days]
    rep.extra_days = sorted(d.date() for d in present_days - set(sessions))

    for session in sessions:
        if session not in present_days:
            continue
        open_, close = cal.session_open(session), cal.session_close(session)  # early closes included
        end = min(close, now)  # a session still in progress is only expected up to now
        expected = pd.date_range(open_, end, freq=spec.length, inclusive="left")
        expected = expected[expected + spec.length <= end]  # only bars that have fully finished
        have = df.index[(df.index >= open_) & (df.index < close)]
        missing = expected.difference(have)
        if len(missing):
            # Merge consecutive missing bars into one gap: "13:35-13:45 (3 bars)".
            breaks = (missing[1:] - missing[:-1]) > spec.length
            starts = [missing[0], *missing[1:][breaks]]
            ends = [*missing[:-1][breaks], missing[-1]]
            for s, e in zip(starts, ends):
                rep.gaps.append((s, e, int((e - s) / spec.length) + 1))
        rep.off_hours_bars += int(((df.index < open_) | (df.index >= close))[local_days == session].sum())

    return rep.finish()


def _fmt_day(d) -> str:
    return pd.Timestamp(d).strftime("%a %Y-%m-%d")


def format_report(rep: QualityReport) -> str:
    if rep.bars == 0:
        return f"{rep.path.name}: empty or missing"
    ny = lambda t: t.tz_convert(SESSION_TZ).strftime("%Y-%m-%d %H:%M")
    lines = [f"{rep.path.name}: {rep.bars} bars, {ny(rep.first)} -> {ny(rep.last)} New York time"]
    checks = [
        ("duplicate timestamps", rep.duplicates),
        ("zero/negative prices", rep.non_positive_prices),
        ("high below low", rep.high_below_low),
        ("close outside high-low", rep.close_outside_range),
        ("zero-volume bars", rep.zero_volume),
        ("bars outside session hours", rep.off_hours_bars),
        ("gaps inside sessions", len(rep.gaps)),
        ("NYSE days missing entirely", len(rep.missing_days)),
        ("bars on non-trading days", len(rep.extra_days)),
    ]
    for name, n in checks:
        lines.append(f"  {'OK  ' if n == 0 else 'FAIL'} {name}: {n}")
    for s, e, n in rep.gaps[:10]:
        lines.append(f"       gap {ny(s)} -> {ny(e)} ({n} bars)")
    if len(rep.gaps) > 10:
        lines.append(f"       ... and {len(rep.gaps) - 10} more gaps")
    for d in rep.missing_days:
        lines.append(f"       missing day {_fmt_day(d)}")
    for d in rep.extra_days:
        lines.append(f"       unexpected day {_fmt_day(d)}")
    return "\n".join(lines)


def run(cache_dir: Path = CACHE_DIR) -> list[QualityReport]:
    reports = []
    for path in sorted(cache_dir.glob("*_*.parquet")):
        if _bar_size_from_name(path) not in BAR_SIZES:
            continue
        reports.append(check_file(path))
        print(format_report(reports[-1]))
    return reports
