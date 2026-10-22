"""A restart between sending an entry and its outcome: the new process finds it at IB and never sends it twice.

The old process is killed (a BaseException out of `ib.sleep`, right after the bracket went out and before
the fill was read). While it is dead IB does what it does: the buy fills, fills in part, never fills, or the stop is hit.
"""

import json

import pandas as pd
import pytest

from live.refs import entry_ref
from live.runner import Runner
from strategies.base import Entry
from tests.test_live_runner import DAY1, ENTRY, Script, at, lines, make

SIGNAL = Entry(stop=99.0, target=105.0, reason="go")  # with a target: stop and target rest as an OCA pair
REF = f"live:ema_trend:QQQ:{DAY1}:1005"


class Crash(BaseException):
    """The process is killed. Not an Exception, so no `except Exception` in the runner can swallow it."""


def killed_after_send(tmp_path, *, signal=SIGNAL, freeze=True):
    """Run the runner until its bracket is at IB, then kill it. Returns (ib, the dead runner)."""
    ib, runner, clock = make(tmp_path, {"10:05": signal}, f"{DAY1} 10:12", last_bar="10:00")
    ib.freeze = freeze
    runner.start()

    def die(seconds):
        raise Crash

    ib.sleep = die  # the first sleep after the bracket is sent is the one in enter()
    with pytest.raises(Crash):
        runner.tick()
    del ib.sleep
    return ib, runner


def restarted(ib, dead, tmp_path, script=None, **kw):
    ib.restart()
    new = Runner(ib, "ema_trend", "QQQ", {}, strategy=Script(script or {"10:05": SIGNAL}), root=tmp_path,
                 clock=dead.clock, feed=dead.feed, **kw)
    new.start()
    return new


def parent(ib):
    return next(t for t in ib._trades if t.order.action == "BUY")


def on_disk(runner):
    return json.loads(runner.state_path.read_text())


def test_the_intent_is_on_disk_before_the_bracket_is_sent(tmp_path):
    ib, dead = killed_after_send(tmp_path)
    saved = on_disk(dead)
    assert saved["pending_entry"]["ref"] == REF and saved["sent_refs"] == [REF] and saved["position"] is None
    assert {o.orderRef for _, o in ib.placed} == {REF} and len(ib.placed) == 3
    assert saved["pending_entry"]["shares"] == parent(ib).order.totalQuantity


def test_restart_when_the_buy_filled_while_dead_adopts_the_position_and_sends_nothing(tmp_path):
    ib, dead = killed_after_send(tmp_path)
    ib.freeze = False
    ib._try_fill(parent(ib))  # the marketable limit fills while nobody is watching
    sent = len(ib.placed)
    new = restarted(ib, dead, tmp_path)
    pos = new.state.position
    assert pos.shares == ib.holdings["QQQ"] > 0 and pos.entry_price == 100.0
    stop, target = (t.order.orderId for t in ib.openTrades())
    assert (pos.stop_order_id, pos.target_order_id) == (stop, target) and (pos.stop, pos.target) == (99.0, 105.0)
    assert new.state.pending_entry is None and not new.state.halted and pd.Timestamp(new.state.last_bar_ts) == at(f"{DAY1} 10:05")
    assert len(ib.placed) == sent and ib.cancelled == []  # nothing new, nothing cancelled: the bracket is simply taken over
    new.tick()
    assert len(ib.placed) == sent


def test_the_adopted_bracket_still_protects_and_the_trade_is_recorded_when_the_stop_hits(tmp_path):
    ib, dead = killed_after_send(tmp_path)
    ib.freeze = False
    ib._try_fill(parent(ib))
    new = restarted(ib, dead, tmp_path)
    ib.now = at(f"{DAY1} 10:14")
    ib.trigger(next(t for t in ib.openTrades() if t.order.orderType == "STP"), 98.9)
    new.clock = new.feed.clock = lambda: at(f"{DAY1} 10:22")  # bars to 10:15 have closed
    new.tick()
    (trade,) = lines(new, "trades.jsonl")
    assert trade["reason"] == "stop" and trade["exit_price"] == 98.9 and new.state.position is None


def test_restart_when_the_buy_never_filled_cancels_it_and_does_not_send_it_again(tmp_path):
    ib, dead = killed_after_send(tmp_path)  # still frozen: the buy rests at IB
    new = restarted(ib, dead, tmp_path)
    assert new.state.position is None and new.state.pending_entry is None and not new.state.halted
    assert ib.openTrades() == [] and ib.holdings.get("QQQ", 0) == 0  # buy cancelled, children went with it
    assert [o.action for _, o in ib.placed] == ["BUY", "SELL", "SELL"]  # still just the original three
    ib.market_price = 100.0  # the market would fill a second try; there must not be one
    new.tick()
    assert len(ib.placed) == 3 and pd.Timestamp(new.state.last_bar_ts) >= at(f"{DAY1} 10:05")


def test_restart_when_the_stop_already_closed_it_notes_it_and_holds_nothing(tmp_path):
    ib, dead = killed_after_send(tmp_path)
    ib.freeze = False
    ib._try_fill(parent(ib))
    ib.trigger(next(t for t in ib.openTrades() if t.order.orderType == "STP"), 98.9)  # bought and stopped out
    sent = len(ib.placed)
    new = restarted(ib, dead, tmp_path)
    assert new.state.position is None and new.state.pending_entry is None and not new.state.halted
    assert len(ib.placed) == sent and ib.holdings["QQQ"] == 0


def test_restart_when_the_buy_filled_only_in_part_resizes_the_protection(tmp_path):
    ib, dead = killed_after_send(tmp_path)
    ib.freeze, ib.fill_fraction = False, 0.5
    ib._try_fill(parent(ib))  # half filled and still working when the process comes back
    held = ib.holdings["QQQ"]
    assert 0 < held < parent(ib).order.totalQuantity
    new = restarted(ib, dead, tmp_path)
    assert new.state.position.shares == held and not new.state.halted
    live = ib.openTrades()
    assert sorted(t.order.orderType for t in live) == ["LMT", "STP"]
    assert {t.order.totalQuantity for t in live} == {held}  # children sized to what filled
    assert {t.order.orderId for t in live} == {new.state.position.stop_order_id, new.state.position.target_order_id}
    assert [o for _, o in ib.placed if o.action == "BUY"] == [parent(ib).order]  # one buy, ever


def test_restart_with_a_state_that_never_got_the_write_ahead_still_will_not_trust_a_stranger_position(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": SIGNAL}, f"{DAY1} 10:12", last_bar="10:00", holdings=7)
    runner.start()
    assert runner.state.halted and "IB holds 7" in runner.state.halted and ib.placed == []


def test_the_second_attempt_at_a_signal_bar_is_refused_by_the_state_even_if_ib_shows_nothing(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": SIGNAL}, f"{DAY1} 10:12", last_bar="10:00")
    runner.start()
    runner.tick()  # enters
    assert len(ib.placed) == 3
    ib.holdings["QQQ"] = 0  # pretend it was all closed and forgotten at IB
    for t in ib._trades:
        t.orderStatus.status = "Cancelled"
    ib._trades.clear()
    runner.state.position = None
    runner.state.last_bar_ts = at(f"{DAY1} 10:00").isoformat()  # the bar is stepped a second time
    runner.tick()
    assert len(ib.placed) == 3
    assert "already sent" in lines(runner, "signals.jsonl")[-1]["action"]


def test_a_ref_is_session_date_plus_signal_bar_time():
    ts = pd.Timestamp("2026-10-22 13:45", tz="UTC")  # 09:45 in New York
    assert entry_ref("live:opening_range_breakout:QQQ", ts) == "live:opening_range_breakout:QQQ:2026-10-22:0945"
