"""Execution: sizing, marketable limits with a timeout, the stop/target pair, exits that must not be silent."""

from types import SimpleNamespace

import pytest
from ib_async import LimitOrder

from live.broker import Broker
from live.execution import DuplicateEntry, Executor, ExecutionHalt
from live.state import PositionState
from strategies.base import Entry
from tests.fake_ib import FakeIB

TAG = "live:ema_trend:QQQ"
REF = TAG + ":2026-03-02:1000"  # one entry signal: session date and signal bar start
QQQ = SimpleNamespace(symbol="QQQ")


def make(dry_run=False, **kw):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    broker = Broker(ib, TAG, dry_run=dry_run)
    kw.setdefault("timeout_s", 2)
    return ib, Executor(ib, broker, QQQ, **kw)


def position(target=None, shares=10):
    return PositionState(shares=shares, entry_time="2026-03-02T15:00:00+00:00", entry_price=100.0,
                         stop=99.0, target=target)


def orders(ib, kind=None):
    return [o for _, o in ib.placed if kind is None or o.orderType == kind]


# -- sizing ------------------------------------------------------------------------------

def test_size_never_risks_more_than_one_percent_even_at_the_price_cap():
    _, ex = make()
    entry = Entry(stop=99.0, target=float("inf"))
    shares = ex.size(entry, 100.0)
    assert shares == 645  # 1000 / 1.55 per share, with the fill taken at the limit cap 100.50 (+ costs)
    worst_fill = 100.5 + 0.005 + 0.01
    assert shares * (worst_fill - 99.0 + 0.02 + 0.015) <= 1000


def test_size_is_capped_at_the_account_with_a_tight_stop():
    _, ex = make()
    assert ex.size(Entry(stop=99.9, target=float("inf")), 100.0) == 994  # no leverage, measured at the price cap


def test_size_is_zero_when_the_stop_is_not_below_the_price():
    _, ex = make()
    assert ex.size(Entry(stop=100.5, target=float("inf")), 100.0) == 0


# -- entry: one bracket --------------------------------------------------------------------

def test_entry_is_one_bracket_parent_limit_plus_stop_sent_together():
    ib, ex = make()
    ib.market_price = 100.0
    got = ex.enter(REF, 10, 100.0, stop=99.0)
    parent, stop = orders(ib)
    assert (parent.action, parent.lmtPrice, parent.tif, parent.transmit) == ("BUY", 100.5, "DAY", False)
    assert (stop.orderType, stop.auxPrice, stop.parentId, stop.transmit, stop.tif) == ("STP", 99.0, parent.orderId, True, "GTC")
    assert {o.orderRef for o in orders(ib)} == {REF}  # every leg carries the idempotent ref
    assert (got.fill.shares, got.fill.price) == (10, 100.0) and got.stop_order_id == stop.orderId
    assert got.target_order_id is None and got.protected and ib.holdings["QQQ"] == 10
    assert [t.orderStatus.status for t in ib._trades] == ["Filled", "Submitted"]  # the stop is live, waiting for a hit


def test_entry_with_a_target_is_a_bracket_with_an_oca_pair_and_one_transmit():
    ib, ex = make()
    ib.market_price = 100.0
    got = ex.enter(REF, 10, 100.0, stop=99.0, target=105.0)
    parent, stop, target = orders(ib)
    assert [o.transmit for o in (parent, stop, target)] == [False, False, True]  # the last order sends all three
    assert stop.parentId == target.parentId == parent.orderId
    assert stop.ocaGroup == target.ocaGroup != "" and stop.ocaType == target.ocaType == 1
    assert (target.orderType, target.lmtPrice, target.tif) == ("LMT", 105.0, "GTC")
    assert (got.stop_order_id, got.target_order_id) == (stop.orderId, target.orderId)
    ib.trigger(ib._trades[1], 99.0)  # the stop is hit: IB cancels the target
    assert ib.holdings["QQQ"] == 0 and ib.openTrades() == []


def test_nothing_is_live_until_the_last_order_of_the_bracket_is_sent():
    ib, ex = make()
    ib.market_price = 100.0
    seen = []
    real = ib.placeOrder
    ib.placeOrder = lambda c, o: (seen.append(ib.holdings.get("QQQ", 0)), real(c, o))[1]
    ex.enter(REF, 10, 100.0, stop=99.0, target=105.0)
    assert seen == [0, 0, 0] and ib.holdings["QQQ"] == 10  # no fill before the whole bracket was out


def test_entry_that_does_not_fill_is_cancelled_with_its_children_and_dropped():
    ib, ex = make()
    ib.market_price, ib.freeze = 100.0, True
    assert ex.enter(REF, 10, 100.0, stop=99.0, target=105.0) is None
    assert len(ib.cancelled) == 1 and ib.openTrades() == [] and ib.holdings.get("QQQ", 0) == 0


def test_partial_entry_sizes_the_children_to_what_filled_and_cancels_the_rest():
    ib, ex = make()
    ib.market_price, ib.fill_fraction = 100.0, 0.5
    got = ex.enter(REF, 10, 100.0, stop=99.0, target=105.0)
    assert got.fill.shares == 5 and ib.holdings["QQQ"] == 5
    live = ib.openTrades()
    assert sorted(t.order.orderType for t in live) == ["LMT", "STP"]
    assert [t.order.totalQuantity for t in live] == [5, 5]
    assert len(ib._trades) == 3  # the children were modified under their own ids, not replaced
    assert [t.order.orderId for t in live] == [got.stop_order_id, got.target_order_id]


def test_a_refused_stop_leaves_no_half_built_bracket_and_the_caller_is_told():
    ib, ex = make()
    ib.market_price, ib.reject_types = 100.0, {"STP"}
    got = ex.enter(REF, 10, 100.0, stop=99.0, target=105.0)
    assert got.fill.shares == 10 and not got.protected and got.stop_order_id is None and got.target_order_id is None
    assert ib.openTrades() == []  # the accepted target was cancelled again


# -- the same ref is never sent twice ---------------------------------------------------------

def test_a_ref_that_filled_is_not_sent_again_even_by_a_restarted_process():
    ib, ex = make()
    ib.market_price = 100.0
    ex.enter(REF, 10, 100.0, stop=99.0)
    sent = len(ib.placed)
    ib.restart()  # new process: done orders are gone from its session list; IB still has the executions
    again = Executor(ib, Broker(ib, TAG), QQQ, timeout_s=2)
    with pytest.raises(DuplicateEntry) as dup:
        again.enter(REF, 10, 100.0, stop=99.0)
    assert dup.value.in_ib and "execution today: BOT 10" in str(dup.value) and len(ib.placed) == sent


def test_a_ref_resting_at_ib_is_not_sent_again():
    ib, ex = make()
    ib.market_price, ib.freeze = 100.0, True
    ex.broker.place(QQQ, LimitOrder("BUY", 10, 100.5), REF)  # sent, not filled
    with pytest.raises(DuplicateEntry, match="open order"):
        ex.enter(REF, 10, 100.0, stop=99.0)
    assert len(ib.placed) == 1


def test_a_ref_only_the_state_file_knows_is_not_sent_either():
    ib, ex = make()
    ib.market_price = 100.0
    with pytest.raises(DuplicateEntry) as dup:
        ex.enter(REF, 10, 100.0, stop=99.0, known_refs=[REF])
    assert not dup.value.in_ib and ib.placed == []


def test_another_signal_bar_has_another_ref_and_is_sent():
    ib, ex = make()
    ib.market_price = 100.0
    ex.enter(REF, 5, 100.0, stop=99.0)
    ib.holdings["QQQ"] = 0
    assert ex.enter(TAG + ":2026-03-02:1005", 5, 100.0, stop=99.0) is not None


def test_the_intent_is_written_after_the_duplicate_check_and_before_the_first_order():
    ib, ex = make()
    ib.market_price = 100.0
    marks = []
    ex.enter(REF, 10, 100.0, stop=99.0, before_send=lambda: marks.append(len(ib.placed)))
    assert marks == [0]
    with pytest.raises(DuplicateEntry):
        ex.enter(REF, 10, 100.0, stop=99.0, before_send=lambda: marks.append("again"))
    assert marks == [0]


# -- protection --------------------------------------------------------------------------

def test_protect_places_a_one_cancels_other_pair_and_records_ids():
    ib, ex = make()
    pos = position(target=105.0)
    ex.protect(pos)
    stop, target = orders(ib)
    assert (stop.orderType, stop.auxPrice, stop.action) == ("STP", 99.0, "SELL")
    assert (target.orderType, target.lmtPrice) == ("LMT", 105.0)
    assert stop.ocaGroup == target.ocaGroup != "" and stop.ocaType == 1
    assert (pos.stop_order_id, pos.target_order_id) == (stop.orderId, target.orderId)


def test_protect_without_target_is_a_lone_stop():
    ib, ex = make()
    pos = position()
    ex.protect(pos)
    assert [o.orderType for o in orders(ib)] == ["STP"] and pos.target_order_id is None


def test_hitting_the_stop_cancels_the_target():
    ib, ex = make()
    ib.holdings["QQQ"] = 10
    pos = position(target=105.0)
    ex.protect(pos)
    stop_trade = ib._trades[0]
    ib.trigger(stop_trade, 99.0)
    assert ib.holdings["QQQ"] == 0 and ib.openTrades() == []


# -- exit --------------------------------------------------------------------------------

def test_exit_cancels_protection_then_sells_everything():
    ib, ex = make()
    ib.holdings["QQQ"] = 10
    ib.market_price = 101.0
    pos = position(target=105.0)
    ex.protect(pos)
    fill = ex.exit(pos, 101.0)
    assert (fill.shares, fill.price) == (10, 101.0)
    sell = orders(ib, "LMT")[-1]
    assert sell.action == "SELL" and sell.lmtPrice == 100.5
    assert ib.holdings["QQQ"] == 0 and ib.openTrades() == []
    assert pos.stop_order_id is not None  # ids stay, so the runner can still look up which order filled


def test_exit_when_the_stop_already_filled_sells_nothing():
    ib, ex = make()
    ib.holdings["QQQ"] = 0
    pos = position()
    assert ex.exit(pos, 101.0) is None
    assert orders(ib, "LMT") == []


def test_stuck_exit_reprices_wider_then_restores_protection_and_halts():
    ib, ex = make(max_reprices=2)
    ib.holdings["QQQ"] = 10
    ib.market_price, ib.freeze = 101.0, True
    pos = position()
    with pytest.raises(ExecutionHalt):
        ex.exit(pos, 100.0)
    sells = orders(ib, "LMT")
    assert [o.lmtPrice for o in sells] == [99.5, 99.0, 98.0]  # 0.5%, 1%, 2% below
    (resting,) = ib.openTrades()  # never left naked: the stop is back
    assert resting.order.orderType == "STP" and pos.stop_order_id == resting.order.orderId


def test_partial_exit_is_completed_by_the_next_attempt():
    ib, ex = make()
    ib.holdings["QQQ"] = 10
    ib.market_price, ib.fractions = 101.0, [0.5]  # first sell fills half, the re-priced one the rest
    fill = ex.exit(position(), 101.0)
    assert fill.shares == 10 and ib.holdings["QQQ"] == 0


# -- dry run -----------------------------------------------------------------------------

def test_dry_run_sends_nothing():
    ib, ex = make(dry_run=True)
    ib.market_price = 100.0
    ib.holdings["QQQ"] = 10
    pos = position(target=105.0)
    assert ex.enter(REF, 10, 100.0, stop=99.0, target=105.0) is None
    ex.protect(pos)
    assert ex.exit(pos, 100.0) is None
    assert ib.placed == [] and pos.stop_order_id is None


# -- races the review found ---------------------------------------------------------------

def sells(ib):
    return [o for o in orders(ib, "LMT") if o.action == "SELL"]


def test_entry_cancel_that_is_not_confirmed_halts_instead_of_reading_it_as_unfilled():
    ib, ex = make()
    ib.market_price, ib.freeze, ib.slow_cancel = 100.0, True, True
    with pytest.raises(ExecutionHalt, match="not confirmed cancelled"):
        ex.enter(REF, 10, 100.0, stop=99.0)
    assert [o.orderType for o in orders(ib)] == ["LMT", "STP"]  # the bracket, nothing else on top


def test_exit_never_has_two_sells_live_when_a_cancel_is_slow():
    ib, ex = make()
    ib.holdings["QQQ"] = 100
    ib.market_price, ib.freeze, ib.slow_cancel = 101.0, True, True
    with pytest.raises(ExecutionHalt, match="not confirmed cancelled"):
        ex.exit(position(shares=100), 101.0)
    assert len(sells(ib)) == 1


def test_exit_halts_before_selling_if_the_stop_cancel_is_not_confirmed():
    ib, ex = make()
    ib.holdings["QQQ"] = 10
    ib.market_price = 101.0
    pos = position()
    ex.protect(pos)
    ib.slow_cancel = True
    with pytest.raises(ExecutionHalt, match="not confirmed cancelled"):
        ex.exit(pos, 101.0)
    assert sells(ib) == []


def test_exit_does_not_sell_shares_the_stop_already_sold_even_if_positions_lag():
    ib, ex = make()
    ib.holdings["QQQ"] = 10
    ib.market_price = 101.0
    pos = position()
    ex.protect(pos)
    ib.trigger(ib._trades[0], 99.0)  # the stop fills...
    ib.holdings["QQQ"] = 10  # ...but IB's position report has not caught up yet
    assert ex.exit(pos, 101.0) is None
    assert sells(ib) == []


def test_exit_of_a_stray_sells_only_its_own_shares():
    ib, ex = make()
    ib.holdings["QQQ"] = 150
    ib.market_price = 101.0
    fill = ex.exit(position(shares=50), 101.0)
    assert fill.shares == 50 and ib.holdings["QQQ"] == 100


def test_protection_is_good_till_cancelled_and_rejection_is_not_silent():
    ib, ex = make()
    pos = position(target=105.0)
    ex.protect(pos)
    assert {o.tif for o in orders(ib)} == {"GTC"}

    ib, ex = make()
    ib.reject_types = {"STP"}
    with pytest.raises(ExecutionHalt, match="did not accept"):
        ex.protect(position(target=105.0))
    assert all(t.isDone() for t in ib._trades)  # the target that was accepted is cancelled again
