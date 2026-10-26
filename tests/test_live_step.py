"""The pure core: engine rules around the strategy, windows, registry, and window/intent parity with the backtester."""

import pandas as pd
import pytest

from backtests.engine import run_backtest
from live.feed import closed_bars
from live.registry import build, parse_settings
from live.state import PositionState
from live.step import step, window
from strategies.base import Entry, Exit, Strategy
from tests.helpers import DAY1, day_bars
from tests.live_helpers import Recorder, cached_bars, position_state


class Fixed(Strategy):
    def __init__(self, intent):
        self.intent, self.seen = intent, []

    def on_bar(self, bars, position):
        self.seen.append(position)
        return self.intent


def held(bars_held=0):
    return PositionState(shares=10, entry_time="2026-03-02T15:00:00+00:00", entry_price=100.0, stop=99.0,
                         target=None, bars_held=bars_held)


BARS = day_bars(DAY1, [100 + i for i in range(10)], 100)


def test_entry_counts_only_when_flat():
    assert step(Fixed(Entry(stop=99.0, target=float("inf"))), BARS, None) is not None
    assert step(Fixed(Entry(stop=99.0, target=float("inf"))), BARS, held()) is None


def test_exit_counts_only_when_long():
    assert step(Fixed(Exit("time")), BARS, None) is None
    assert step(Fixed(Exit("time")), BARS, held()) == Exit("time")


def test_bars_held_is_raised_before_the_call_and_kept():
    pos, strategy = held(bars_held=2), Fixed(None)
    step(strategy, BARS, pos)
    assert strategy.seen[0].bars_held == 3 and pos.bars_held == 3


def test_flat_position_is_passed_as_none():
    strategy = Fixed(None)
    step(strategy, BARS, None)
    assert strategy.seen == [None]


def test_window_ends_at_the_bar_and_is_capped():
    full = pd.concat([day_bars(DAY1, [100] * 78, 100)])
    w = window(full, full.index[49], lookback=20)
    assert len(w) == 20 and w.index[-1] == full.index[49] and w.index[0] == full.index[30]
    assert len(window(full, full.index[5], lookback=20)) == 6  # early in the data: shorter, as in the backtest


def test_registry_builds_like_the_judge_does():
    s = build("opening_range_breakout", parse_settings(["range_minutes=15", "target_r=1.0"]))
    assert s.p.range_minutes == 15 and s.p.target_r == 1.0
    with pytest.raises(ValueError):
        build("nope", {})
    with pytest.raises(ValueError):
        parse_settings(["range_minutes"])


def test_parse_settings_types():
    out = parse_settings(["a=15", "b=1.5", "c=None", "d=11:30", "e=text"])
    assert out["a"] == 15 and out["b"] == 1.5 and out["c"] is None
    assert str(out["d"]) == "11:30:00" and out["e"] == "text"


CASES = [
    ("opening_range_breakout", {"range_minutes": 15, "target_r": 1.0}),
    ("ema_trend", {}),
    ("bollinger_rsi_reversion", {}),
]


@pytest.mark.parametrize("name,settings", CASES)
def test_windows_and_intents_match_the_backtester(name, settings):
    """Rebuild every on_bar call of a backtest from a polled, lagged feed: same window, same decision."""
    bars = cached_bars()
    rec = Recorder(build(name, settings))
    run_backtest(bars, rec)
    assert rec.signals(), "the slice must contain trades or the test proves nothing"

    for delay_minutes in (0, 20):  # bars visible the moment they close, and 20 minutes late
        fresh = build(name, settings)
        for n, (ts, position, intent) in enumerate(rec.calls):
            if n % 25 and position is None and intent is None:
                continue  # every call with a position or a decision, plus a sample of the quiet ones
            now = ts + pd.Timedelta(minutes=5 + delay_minutes)  # the moment this bar first becomes visible
            raw = bars.loc[now - pd.Timedelta(days=7) : now + pd.Timedelta(minutes=30)]  # IB also sends the forming bar
            got = window(closed_bars(raw, now, 0), ts)
            i = bars.index.get_loc(ts)
            pd.testing.assert_frame_equal(got, bars.iloc[max(0, i + 1 - 200) : i + 1])
            assert step(fresh, got, position_state(position)) == intent  # the engine only recorded legal intents
