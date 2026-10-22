"""Partial fills, IB refusals and a dropped connection, against the fake IB."""

import json
from types import SimpleNamespace

import pytest

from live.broker import Broker, PaperGuardError, reconnect_paper
from live.execution import ExecutionHalt, Executor, OrderRejected
from live.runner import Runner
from live.state import PositionState
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_runner import ENTRY, Script, held, make

TAG = "live:ema_trend:QQQ"
REF = TAG + ":2026-03-02:1000"
QQQ = SimpleNamespace(symbol="QQQ")


def executor(**kw):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    kw.setdefault("timeout_s", 2)
    return ib, Executor(ib, Broker(ib, TAG), QQQ, **kw)


def pos(shares=10, target=None):
    return PositionState(shares=shares, entry_time="2026-03-02T15:00:00+00:00", entry_price=100.0, stop=99.0, target=target)


# -- partial fills -----------------------------------------------------------------------------

def test_a_partly_sold_exit_that_then_stalls_protects_only_what_is_left_and_halts():
    ib, ex = executor(max_reprices=1)
    ib.holdings["QQQ"] = 10
    ib.market_price, ib.fractions = 101.0, [0.5]
    real = ib._try_fill
    ib._try_fill = lambda trade: (real(trade), setattr(ib, "freeze", True))[0]  # the market stops filling after 5
    p = pos()
    with pytest.raises(ExecutionHalt, match="could not sell 5"):
        ex.exit(p, 101.0)
    assert ib.holdings["QQQ"] == 5
    (resting,) = ib.openTrades()
    assert (resting.order.orderType, resting.order.totalQuantity) == ("STP", 5)  # a stop for the shares still held


def test_a_stop_that_fills_only_in_part_while_the_runner_watches_halts_it(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:05", position=held(), holdings=10)
    runner.start()
    stop = next(t for t in ib.openTrades() if t.order.orderType == "STP")
    ib._fill(stop, 4, 98.9)  # 4 of 10 sold, the rest still resting
    runner.tick()
    assert "IB holds 6" in runner.state.halted and runner.state.position is not None


# -- IB refuses an order ----------------------------------------------------------------------------

def test_a_refused_price_increment_is_logged_with_ibs_code_and_reason_and_not_retried(caplog):
    ib, ex = executor()
    ib.market_price = 100.0
    ex._limit = lambda action, ref, widen=0: 99.995  # SPY and QQQ trade in cents
    with pytest.raises(OrderRejected, match=r"\[110\].*minimum price variation"):
        ex.enter(REF, 10, 100.0, stop=99.0)
    assert [o.orderType for _, o in ib.placed] == ["LMT", "STP"]  # the bracket once; nothing re-sent
    assert ex.broker.sent == 2 and ib.openTrades() == [] and ib.holdings.get("QQQ", 0) == 0
    assert "IB REJECTED" in caplog.text and "[110]" in caplog.text


def test_a_refused_sell_is_not_re_priced_and_the_stop_goes_back():
    ib, ex = executor()
    ib.holdings["QQQ"] = 10
    ib.market_price = 101.0
    p = pos()
    ex.protect(p)
    ib.reject_types = {"LMT"}
    with pytest.raises(OrderRejected, match=r"\[201\]"):
        ex.exit(p, 101.0)
    sells = [o for _, o in ib.placed if o.orderType == "LMT"]
    assert len(sells) == 1  # before: four attempts, each wider, each refused
    (resting,) = ib.openTrades()
    assert resting.order.orderType == "STP" and p.stop_order_id == resting.order.orderId and ib.holdings["QQQ"] == 10


def test_a_refused_stop_names_ibs_reason():
    ib, ex = executor()
    ib.reject_types = {"STP"}
    with pytest.raises(OrderRejected, match=r"refused the stop.*\[201\]"):
        ex.enter(REF, 10, 100.0, stop=99.0)


def test_warnings_and_cancel_notices_are_not_refusals():
    ib = FakeIB()
    broker = Broker(ib, TAG)
    for code in (202, 399, 2104, 2158):
        ib.errorEvent.emit(7, code, "info", None)
    ib.errorEvent.emit(-1, 110, "not about an order", None)
    assert broker.errors == {}
    ib.errorEvent.emit(7, 110, "bad price", None)
    assert broker.errors == {7: (110, "bad price")}


def test_the_runner_halts_on_a_refused_entry_and_sends_nothing_more(tmp_path):
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY, "10:10": ENTRY}, f"{DAY1} 10:17", last_bar="10:00")
    ib.reject_types = {"LMT"}
    runner.start()
    runner.tick()
    assert "IB rejected BUY" in runner.state.halted and "[201]" in runner.state.halted
    sent = len(ib.placed)
    assert sent == 2  # the bracket, once
    runner.tick()
    runner.tick()
    assert len(ib.placed) == sent and runner.state.position is None


# -- the connection drops --------------------------------------------------------------------------------

def dropping(tmp_path, *, fail=0, error=ConnectionRefusedError("gateway down"), **kw):
    """A runner whose `reconnect` fails `fail` times, then brings the fake IB back. Returns (ib, runner, calls)."""
    calls = []
    ib_box = {}

    def reconnect():
        calls.append(1)
        if len(calls) <= fail:
            raise error
        ib_box["ib"].restart()  # IB kept its orders and fills; the new session forgot the finished ones
        ib_box["ib"].connected = True

    ib, runner, clock = make(tmp_path, kw.pop("script", {}), kw.pop("now", f"{DAY1} 10:12"),
                             last_bar=kw.pop("last_bar", "10:00"), reconnect=reconnect, reconnect_wait_s=0, **kw)
    ib_box["ib"] = ib
    return ib, runner, calls


def test_a_drop_while_flat_reconnects_checks_the_state_and_carries_on(tmp_path):
    ib, runner, calls = dropping(tmp_path)
    runner.start()
    ib.connected = False
    runner.tick()
    assert calls == [1] and not runner.state.halted
    assert runner.state.last_bar_ts is not None and runner.state.last_bar_ts.startswith("2026-03-02")


def test_a_drop_with_a_position_keeps_its_resting_stop_and_sends_nothing_new(tmp_path):
    ib, runner, calls = dropping(tmp_path, last_bar="10:05", position=held(), holdings=10)
    runner.start()  # places the stop
    placed = len(ib.placed)
    ib.connected = False
    runner.tick()
    assert calls == [1] and not runner.state.halted and runner.state.position.shares == 10
    assert len(ib.placed) == placed and ib.cancelled == []  # the stop was resting at IB all along


def test_a_stop_that_filled_while_disconnected_is_found_by_the_reconcile(tmp_path):
    ib, runner, calls = dropping(tmp_path, last_bar="10:05", position=held(), holdings=10)
    runner.start()
    ib.connected = False
    ib.trigger(next(t for t in ib.openTrades() if t.order.orderType == "STP"), 98.9)
    runner.tick()
    assert runner.state.position is None and not runner.state.halted


def test_reconnect_is_retried_a_few_times_then_carries_on(tmp_path):
    ib, runner, calls = dropping(tmp_path, fail=2)
    runner.start()
    ib.connected = False
    runner.tick()
    assert len(calls) == 3 and not runner.state.halted


def test_no_connection_after_all_attempts_halts_and_trades_nothing(tmp_path):
    ib, runner, calls = dropping(tmp_path, fail=99, reconnect_attempts=3, script={"10:05": ENTRY})
    runner.start()
    ib.connected = False
    runner.tick()
    assert len(calls) == 3 and "3 reconnect attempts failed" in runner.state.halted
    runner.tick()
    assert len(calls) == 3 and ib.placed == []  # halted: no more attempts, no orders


def test_a_non_paper_account_on_the_new_connection_halts_at_once(tmp_path):
    ib, runner, calls = dropping(tmp_path, fail=99, error=PaperGuardError("Non-paper account(s): ['U1']"))
    runner.start()
    ib.connected = False
    runner.tick()
    assert len(calls) == 1 and "paper guard on reconnect" in runner.state.halted


def test_a_position_nobody_explains_after_the_reconnect_halts(tmp_path):
    ib, runner, calls = dropping(tmp_path)
    runner.start()
    ib.connected = False
    ib.holdings["QQQ"] = 7
    runner.tick()
    assert "IB holds 7" in runner.state.halted and ib.placed == []


def test_a_drop_in_the_middle_of_an_entry_halts_and_the_next_start_finds_the_entry(tmp_path):
    ib, runner, calls = dropping(tmp_path, script={"10:05": ENTRY})
    runner.start()
    ib.freeze = True
    ib.sleep = lambda s: setattr(ib, "connected", False)  # the line goes down right after the bracket is sent
    runner.tick()
    assert "Not connected" in runner.state.halted
    assert runner.state.pending_entry and json.loads(runner.state_path.read_text())["pending_entry"]["ref"]
    del ib.sleep
    ib.connected = True
    ib.restart()
    again = Runner(ib, "ema_trend", "QQQ", {}, strategy=Script({}), root=tmp_path, clock=runner.clock,
                   feed=runner.feed, clear_halt=True)
    again.start()  # the buy rests unfilled at IB: it is cancelled and nothing is held or re-sent
    assert again.state.pending_entry is None and again.state.position is None and not again.state.halted
    assert ib.openTrades() == [] and len(ib.placed) == 2


def test_reconnect_paper_uses_the_same_checks_as_the_first_connect():
    ib = FakeIB()
    reconnect_paper(ib, port=7497)
    assert ib.connected and ib.connect_args[:3] == ("127.0.0.1", 7497, 6) and ib.market_data_type == 3
    for host, port in (("192.168.1.5", 4002), ("127.0.0.1", 4003)):
        with pytest.raises(PaperGuardError):
            reconnect_paper(FakeIB(), host=host, port=port)
    bad = FakeIB(accounts=("U1234567",))
    with pytest.raises(PaperGuardError):
        reconnect_paper(bad)
    assert not bad.connected  # disconnected again


def test_the_reject_probe_reports_one_refusal_with_ibs_code_and_no_retry():
    from live.probe import probe_reject
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    lines = []
    assert probe_reject(ib, symbol="SPY", price=762.0, risk=None, out=lines.append) == 0
    text = "\n".join(lines)
    assert "REFUSED BY IB" in text and "[110]" in text and "orders sent by this run: 1" in text
    assert [o.lmtPrice for _, o in ib.placed] == [685.805]
