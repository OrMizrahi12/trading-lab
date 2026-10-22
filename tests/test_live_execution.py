"""Execution: sizing, marketable limits with a timeout, the stop/target pair, exits that must not be silent."""

from types import SimpleNamespace

import pytest

from live.broker import Broker
from live.execution import Executor, ExecutionHalt
from live.state import PositionState
from strategies.base import Entry
from tests.fake_ib import FakeIB

TAG = "live:ema_trend:QQQ"
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


# -- entry -------------------------------------------------------------------------------

def test_buy_is_a_limit_through_the_market_and_fills_at_the_market():
    ib, ex = make()
    ib.market_price = 100.0
    fill = ex.buy(10, 100.0)
    assert (fill.shares, fill.price) == (10, 100.0)
    (order,) = orders(ib)
    assert order.action == "BUY" and order.lmtPrice == 100.5 and order.tif == "DAY" and order.orderRef == TAG
    assert ib.holdings["QQQ"] == 10


def test_buy_that_does_not_fill_is_cancelled_and_dropped():
    ib, ex = make()
    ib.market_price, ib.freeze = 100.0, True
    assert ex.buy(10, 100.0) is None
    assert len(ib.cancelled) == 1 and ib.openTrades() == [] and ib.holdings.get("QQQ", 0) == 0


def test_partial_entry_keeps_what_filled_and_cancels_the_rest():
    ib, ex = make()
    ib.market_price, ib.fill_fraction = 100.0, 0.5
    fill = ex.buy(10, 100.0)
    assert fill.shares == 5 and ib.holdings["QQQ"] == 5
    assert len(ib.cancelled) == 1 and ib.openTrades() == []


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
    assert ex.buy(10, 100.0) is None
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
        ex.buy(10, 100.0)
    assert len(orders(ib)) == 1  # nothing else was sent on top


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
