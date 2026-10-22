"""Runner scenarios on hand-made bars: first run, restart, stale signals, wall-clock flatten, halts, dry run."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from live.feed import BAR, BarFeed
from live.runner import Runner
from live.state import LiveState, PositionState, save, settings_key
from strategies.base import Entry, Exit, Strategy
from tests.fake_ib import FakeIB, FakeTrade
from tests.helpers import DAY1, DAY2, TZ, day_bars, two_days


def at(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=TZ).tz_convert("UTC")


class Script(Strategy):
    """Returns the scripted intent for a bar (by New York start time, e.g. '10:00'), else None."""

    def __init__(self, script):
        self.script = script

    def on_bar(self, bars, position):
        return self.script.get(f"{bars.index[-1].tz_convert(TZ):%H:%M}")


BARS = day_bars(DAY1, [100.0] * 78, 100.0)  # a full flat session
ENTRY = Entry(stop=99.0, target=float("inf"), reason="go")


def make(tmp_path, script, now, *, bars=BARS, last_bar=None, position=None, holdings=0, dry_run=False, **kw):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.holdings["QQQ"] = holdings
    ib.market_price = 100.0
    clock = [at(now)]
    feed = BarFeed(ib, None, clock=lambda: clock[0], fetch=lambda *a: bars)
    runner = Runner(ib, "ema_trend", "QQQ", {}, strategy=Script(script), root=tmp_path, clock=lambda: clock[0],
                    feed=feed, dry_run=dry_run, **kw)
    if last_bar or position:
        state = LiveState("ema_trend", "QQQ", settings_key("ema_trend", "QQQ", {}),
                          last_bar_ts=at(f"{DAY1} {last_bar}").isoformat() if last_bar else None, position=position)
        save(runner.state_path, state)
    return ib, runner, clock


def held(entry_time=None, **kw):
    return PositionState(shares=10, entry_time=(entry_time or at(f"{DAY1} 10:00")).isoformat(), entry_price=100.0,
                         stop=99.0, target=None, **kw)


def lines(runner, name):
    path = runner.dir / name
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_first_run_trades_nothing_on_history_and_remembers_the_newest_bar(tmp_path):
    ib, runner, _ = make(tmp_path, {f"{h:02d}:{m:02d}": ENTRY for h in range(9, 16) for m in range(0, 60, 5)},
                         f"{DAY1} 10:12")
    runner.start()
    runner.tick()
    assert ib.placed == []
    assert pd.Timestamp(runner.state.last_bar_ts) == at(f"{DAY1} 10:05")  # closed 10:10; 10:10 is still forming


def test_fresh_signal_buys_and_protects_then_saves_state(tmp_path):
    ib, runner, clock = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00")
    runner.start()
    runner.tick()
    assert [o.action + o.orderType for _, o in ib.placed] == ["BUYLMT", "SELLSTP"]
    pos = runner.state.position
    assert pos.shares > 0 and pos.entry_time == at(f"{DAY1} 10:10").isoformat() and pos.stop_order_id is not None
    on_disk = json.loads(runner.state_path.read_text())
    assert on_disk["position"]["shares"] == pos.shares and on_disk["last_bar_ts"] == at(f"{DAY1} 10:05").isoformat()
    assert lines(runner, "signals.jsonl")[0]["action"] == "bought"


def test_stale_entry_is_skipped_but_a_stale_exit_is_still_sold(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:00": ENTRY}, f"{DAY1} 11:00", last_bar="09:55")  # 55 minutes late
    runner.start()
    runner.tick()
    assert ib.placed == [] and "old" in lines(runner, "signals.jsonl")[0]["action"]

    ib, runner, _ = make(tmp_path / "b", {"10:00": Exit("time")}, f"{DAY1} 11:00", last_bar="09:55",
                         position=held(), holdings=10)
    runner.start()
    runner.tick()
    assert runner.state.position is None and ib.holdings["QQQ"] == 0
    assert lines(runner, "trades.jsonl")[0]["reason"] == "time"


def test_restart_mid_position_reattaches_without_new_orders(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:05", position=held(), holdings=10)
    order = SimpleNamespace(orderId=41, orderRef=runner.tag, action="SELL", totalQuantity=10)
    ib._trades.append(FakeTrade(SimpleNamespace(symbol="QQQ"), order))
    runner.state_path.write_text(runner.state_path.read_text().replace('"stop_order_id": null', '"stop_order_id": 41'))
    runner.start()
    runner.tick()
    assert ib.placed == [] and ib.cancelled == [] and runner.state.position.stop_order_id == 41


def test_restart_with_missing_stop_places_a_new_one(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:05", position=held(), holdings=10)
    runner.start()
    assert [o.orderType for _, o in ib.placed] == ["STP"] and runner.state.position.stop_order_id


def test_stop_hit_closes_the_position_on_the_bar_that_contains_it(tmp_path):
    ib, runner, clock = make(tmp_path, {}, f"{DAY1} 10:22", last_bar="10:05", position=held())
    ib.holdings["QQQ"] = 10
    runner.start()  # places the stop
    stop = ib._trades[0]
    ib.now = at(f"{DAY1} 10:14")  # the stop fills inside the bar that starts 10:10
    ib.trigger(stop, 98.9)
    runner.tick()  # sees bars 10:10 and 10:15 (closed 10:20)
    (trade,) = lines(runner, "trades.jsonl")
    assert trade["reason"] == "stop" and trade["exit_price"] == 98.9
    assert runner.state.position is None
    assert pd.Timestamp(trade["exit_time"]) == at(f"{DAY1} 10:14")


def test_wall_clock_flattens_before_the_close(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 15:51", last_bar="15:45", position=held(), holdings=10)
    runner.start()
    runner.tick()
    assert runner.state.position is None and ib.holdings["QQQ"] == 0
    assert lines(runner, "trades.jsonl")[0]["reason"] == "flatten_deadline"


def test_no_flatten_before_the_deadline(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 15:40", last_bar="15:30", position=held(), holdings=10)
    runner.start()
    runner.tick()
    assert runner.state.position is not None


def test_position_from_yesterday_is_flattened_at_once(tmp_path):
    bars = two_days([100.0] * 78, [100.0] * 20)
    ib, runner, _ = make(tmp_path, {}, f"{DAY2} 11:15", bars=bars, position=held(entry_time=at(f"{DAY1} 15:00")),
                         holdings=10)
    runner.start()
    runner.tick()
    assert lines(runner, "trades.jsonl")[0]["reason"] == "overnight"


def test_no_new_entry_after_the_flatten_deadline(tmp_path):
    ib, runner, _ = make(tmp_path, {"15:45": ENTRY}, f"{DAY1} 15:51", last_bar="15:40")
    runner.start()
    runner.tick()
    assert ib.placed == [] and "deadline" in lines(runner, "signals.jsonl")[0]["action"]


def test_unexplained_position_halts_and_trades_nothing_until_fixed(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", holdings=7)
    runner.start()
    runner.tick()
    assert runner.state.halted and "IB holds 7" in runner.state.halted and ib.placed == []

    ib.holdings["QQQ"] = 0  # a human flattened it; a restart finds IB and the state file agreeing again
    again = Runner(ib, "ema_trend", "QQQ", {}, strategy=Script({}), root=tmp_path, clock=runner.clock, feed=runner.feed)
    again.start()
    assert again.state.halted is None


def test_flatten_on_mismatch_sells_the_stray_shares(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", holdings=7, flatten_on_mismatch=True)
    runner.start()
    assert ib.holdings["QQQ"] == 0 and not runner.state.halted


def test_stuck_exit_halts_the_engine_and_keeps_the_stop(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": Exit("time")}, f"{DAY1} 10:12", last_bar="10:00", position=held(),
                         holdings=10)
    runner.start()
    ib.freeze = True
    runner.executor.timeout_s = 1
    runner.tick()
    assert "could not sell" in runner.state.halted
    assert any(t.order.orderType == "STP" for t in ib.openTrades())
    runner.tick()  # halted: nothing more happens
    assert len([1 for _, o in ib.placed if o.action == "SELL" and o.orderType == "LMT"]) == 4


def test_dry_run_logs_decisions_but_sends_nothing_and_keeps_its_own_state_file(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", dry_run=True)
    runner.start()
    runner.tick()
    assert ib.placed == [] and runner.state.position is None
    assert runner.state_path.name == "state_dryrun.json" and not (runner.dir / "state.json").exists()
    assert lines(runner, "signals.jsonl")[0]["kind"] == "entry"


def test_dry_run_catch_up_steps_the_last_bars_on_a_fresh_state(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:00": ENTRY, "10:05": Exit("time")}, f"{DAY1} 10:12", dry_run=True, catch_up=3)
    runner.start()
    runner.tick()
    assert [x["bar"] for x in lines(runner, "signals.jsonl")] == [at(f"{DAY1} 10:00").isoformat()]


def test_state_of_other_settings_is_refused(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00")
    other = Runner(ib, "ema_trend", "QQQ", {"fast_period": 30}, strategy=Script({}), root=tmp_path)
    other.state_path.parent.mkdir(parents=True, exist_ok=True)
    other.state_path.write_text(runner.state_path.read_text())
    from live.state import StateError
    with pytest.raises(StateError):
        other.start()
