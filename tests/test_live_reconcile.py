"""Reconcile on start: every case between the state file and IB."""

from types import SimpleNamespace

from live.broker import Broker
from live.reconcile import reconcile
from live.state import LiveState, PositionState
from tests.fake_ib import FakeIB, FakeTrade

TAG = "live:ema_trend:QQQ"
QQQ = SimpleNamespace(symbol="QQQ")


def make(shares_state=None, shares_ib=0, orders=(), target=None):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.holdings["QQQ"] = shares_ib
    for oid, ref, action in orders:
        order = SimpleNamespace(orderId=oid, orderRef=ref, action=action, totalQuantity=10)
        ib.trades.append(FakeTrade(QQQ, order))
    pos = None
    if shares_state:
        pos = PositionState(shares=shares_state, entry_time="2026-03-02T15:00:00+00:00", entry_price=100.0,
                            stop=99.0, target=target, stop_order_id=1,
                            target_order_id=2 if target else None)
    state = LiveState("ema_trend", "QQQ", "k", position=pos)
    return ib, Broker(ib, TAG), state


def test_flat_and_flat_is_ok():
    ib, broker, state = make()
    res = reconcile(ib, broker, state)
    assert res.ok and not res.actions and not res.needs_protection


def test_flat_state_but_ib_long_is_a_problem_and_nothing_is_traded():
    ib, broker, state = make(shares_ib=10)
    res = reconcile(ib, broker, state)
    assert not res.ok and "IB holds 10" in res.problems[0]
    assert ib.placed == []


def test_stale_own_orders_are_cancelled_when_flat():
    ib, broker, state = make(orders=[(5, TAG, "SELL")])
    res = reconcile(ib, broker, state)
    assert res.ok and len(ib.cancelled) == 1 and ib.openTrades() == []


def test_foreign_order_is_a_problem_and_left_alone():
    ib, broker, state = make(orders=[(5, "someone-else", "BUY")])
    res = reconcile(ib, broker, state)
    assert not res.ok and ib.cancelled == []


def test_long_with_intact_stop_is_re_attached_untouched():
    ib, broker, state = make(shares_state=10, shares_ib=10, orders=[(1, TAG, "SELL")])
    res = reconcile(ib, broker, state)
    assert res.ok and not res.needs_protection and ib.cancelled == []


def test_long_with_intact_stop_and_target():
    ib, broker, state = make(shares_state=10, shares_ib=10, target=105.0,
                             orders=[(1, TAG, "SELL"), (2, TAG, "SELL")])
    res = reconcile(ib, broker, state)
    assert res.ok and not res.needs_protection


def test_long_with_missing_stop_asks_for_protection():
    ib, broker, state = make(shares_state=10, shares_ib=10)
    res = reconcile(ib, broker, state)
    assert res.ok and res.needs_protection and state.position.stop_order_id is None


def test_long_with_only_one_leg_left_cancels_it_and_asks_for_protection():
    ib, broker, state = make(shares_state=10, shares_ib=10, target=105.0, orders=[(2, TAG, "SELL")])
    res = reconcile(ib, broker, state)
    assert res.ok and res.needs_protection and len(ib.cancelled) == 1


def test_share_mismatch_is_a_problem():
    ib, broker, state = make(shares_state=10, shares_ib=4, orders=[(1, TAG, "SELL")])
    res = reconcile(ib, broker, state)
    assert not res.ok and ib.cancelled == []


def test_position_gone_at_ib_sets_state_flat():
    ib, broker, state = make(shares_state=10, shares_ib=0)
    res = reconcile(ib, broker, state)
    assert res.ok and state.position is None and ib.placed == []
