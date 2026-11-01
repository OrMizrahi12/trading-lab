"""Parity: cached bars replayed through the whole live path must give the backtester's decisions, exactly.

The live path here is the real one: BarFeed (closed bars only, window), Runner, step, Executor, Broker and the
state file, talking to a SimIB that fills by the backtester's rules. Every on_bar call is compared with the one
`run_backtest` made on the same bar: the bar, the position the strategy was shown (entry price, stop, target,
bars held) and the decision. The wall-clock flatten deadline is off (it is the one deliberate difference).
"""

import json

import pandas as pd
import pytest

from backtests.engine import run_backtest
from live.feed import BAR, BarFeed
from live.registry import build
from live.runner import Runner
from tests.live_helpers import Recorder, cached_bars
from tests.sim_ib import SimIB

CASES = [
    ("opening_range_breakout", {"range_minutes": 15, "target_r": 1.0}, 0),
    ("opening_range_breakout", {"range_minutes": 15, "target_r": 1.0}, 20),
    ("ema_trend", {}, 0),
    ("bollinger_rsi_reversion", {}, 0),
]


def replay(name, settings, bars, delay_minutes, root):
    ib = SimIB(bars)
    rec = Recorder(build(name, settings))
    now = [bars.index[0]]

    def fetch(_ib, _contract, end, _size, _throttle):
        # What IB would send: about a week of bars. With no lag the forming bar comes too (the feed must drop it);
        # with a lag a bar is only visible once it is that late, so nothing newer than bar i exists yet.
        raw = bars.iloc[max(0, ib.i - 700) : ib.i + (2 if delay_minutes == 0 else 1)]
        return raw[raw.index >= end - pd.Timedelta(days=7)]

    feed = BarFeed(ib, None, clock=lambda: now[0], fetch=fetch,
                   **({"settle_min": 0} if delay_minutes == 0 else {}))  # real-time data needs no settling; the lag case uses the default
    runner = Runner(ib, name, "QQQ", settings, strategy=rec, root=root, clock=lambda: now[0], feed=feed,
                    wall_clock_rules=False)
    runner.start()
    runner.state.last_bar_ts = (bars.index[0] - BAR).isoformat()  # step from the first bar, as the backtest does
    for i in range(len(bars) - 1):  # the backtest never asks about the last bar
        ib.advance(i)
        now[0] = bars.index[i] + BAR + pd.Timedelta(minutes=delay_minutes)  # when this bar becomes visible
        runner.tick()
        assert not runner.state.halted, runner.state.halted
    return rec, runner


def first_difference(a, b):
    for n, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return n, x, y
    return None


@pytest.mark.parametrize("name,settings,delay", CASES)
def test_live_path_makes_the_backtests_decisions(name, settings, delay, tmp_path):
    bars = cached_bars(start="2025-09-02", end="2025-10-24")
    engine = Recorder(build(name, settings))
    result = run_backtest(bars, engine)
    assert engine.signals(), "the slice must contain decisions or the test proves nothing"

    live, runner = replay(name, settings, bars, delay, tmp_path)

    assert len(live.calls) == len(engine.calls)
    assert first_difference(live.calls, engine.calls) is None  # bar, position shown, decision: all of it
    assert live.signals() == engine.signals()

    # Extra check: the trades the runner logged are the backtester's trades (times and prices; share counts differ,
    # the runner sizes on the signal bar's close while the backtester sizes on the next open).
    logged = [json.loads(line) for line in (runner.dir / "trades.jsonl").read_text().splitlines()]
    expected = result.trades[result.trades["reason"] != "end_of_data"]
    assert len(logged) == len(expected)
    for got, (_, want) in zip(logged, expected.iterrows()):
        assert pd.Timestamp(got["entry_time"]) == want["entry_time"]
        assert pd.Timestamp(got["exit_time"]) == want["exit_time"]
        assert got["entry_price"] == pytest.approx(want["entry_price"])
        if want["reason"] != "overnight":
            assert got["reason"] == want["reason"]
            assert got["exit_price"] == pytest.approx(want["exit_price"], abs=0.01)  # IB prices stops in whole cents
