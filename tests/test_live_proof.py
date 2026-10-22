"""The one-share proof command, against the fake IB: run it twice, the second run sends nothing."""

import pytest

from live.broker import PaperGuardError
from live.proof import prove
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_runner import at


def paper(price=100.0):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.market_price = price
    return ib


def run(ib, now=f"{DAY1} 10:30", **kw):
    lines = []
    code = prove(ib, symbol="SPY", price=lambda: 100.0, clock=lambda: at(now), out=lines.append, **kw)
    return code, "\n".join(lines)


def test_first_run_sends_one_bracket_and_ends_flat_second_run_sends_nothing():
    ib = paper()
    code, text = run(ib)
    assert code == 0, text
    parent, stop, target, sell = (o for _, o in ib.placed)
    ref = f"live:proof_entry:SPY:{DAY1}:0930"
    assert [o.orderRef for o in (parent, stop, target)] == [ref] * 3
    assert (parent.action, parent.totalQuantity, parent.transmit) == ("BUY", 1, False)
    assert (stop.parentId, target.parentId, stop.auxPrice, target.lmtPrice) == (parent.orderId,) * 2 + (98.0, 102.0)
    assert sell.action == "SELL" and sell.totalQuantity == 1
    assert ib.holdings["SPY"] == 0 and ib.openTrades() == []
    assert "orders sent by this run: 4" in text and "flat, no open orders" in text and "RESULT    : OK" in text

    ib.restart()  # a new process, hours later the same day
    placed = len(ib.placed)
    code, text = run(ib, now=f"{DAY1} 14:30")
    assert code == 0, text
    assert len(ib.placed) == placed  # nothing new went out
    assert "NOTHING is sent" in text and "execution today: BOT 1" in text
    assert "orders sent by this run: 0" in text and "the ref was not sent twice" in text


def test_a_different_day_is_a_different_ref():
    ib = paper()
    assert run(ib)[0] == 0
    ib.restart()
    placed = len(ib.placed)
    assert run(ib, now="2026-03-03 15:30")[0] == 0 and len(ib.placed) > placed


def test_refuses_when_the_market_is_closed_or_about_to_close():
    for now in (f"{DAY1} 08:00", f"{DAY1} 15:55", "2026-03-01 11:00"):  # pre-open, last minutes, a Sunday
        ib = paper()
        code, text = run(ib, now=now)
        assert code == 2 and "market is closed" in text and ib.placed == []


def test_refuses_when_the_symbol_is_not_flat():
    ib = paper()
    ib.holdings["SPY"] = 3
    code, text = run(ib)
    assert code == 2 and "not flat" in text and ib.placed == []


def test_refuses_a_non_paper_account():
    ib = FakeIB(accounts=("U1234567",))
    ib.connect("127.0.0.1", 4002)
    with pytest.raises(PaperGuardError):
        run(ib)
    assert ib.placed == []


def test_an_entry_that_does_not_fill_leaves_nothing_resting():
    ib = paper()
    ib.freeze = True
    code, text = run(ib, executor=None)
    assert code == 0 and "not filled" in text and ib.openTrades() == [] and ib.holdings.get("SPY", 0) == 0


def test_orders_of_an_earlier_session_are_named_by_permid_not_order_zero():
    from types import SimpleNamespace

    from live.refs import evidence, order_label
    from tests.fake_ib import FakeTrade

    old = SimpleNamespace(orderId=0, permId=1234567, orderRef="r", action="BUY", orderType="LMT", totalQuantity=1)
    assert order_label(old) == "permId 1234567"
    assert order_label(SimpleNamespace(orderId=7, permId=0)) == "order #7"  # a fresh order has no permId yet
    ib = paper()
    ib._trades.append(FakeTrade(SimpleNamespace(symbol="SPY"), old))
    (line,) = evidence(ib, "r")
    assert "permId 1234567" in line and "#0" not in line


def test_library_log_lines_go_to_the_file_not_the_console(tmp_path, capsys):
    import logging

    from live.proof import setup_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        root.addHandler(logging.StreamHandler())  # e.g. a basicConfig somebody else did
        setup_logging(tmp_path)
        logging.getLogger("ib_async.wrapper").info("Warning 2104, Market data farm connection is OK")
        logging.getLogger("live").warning("something worth keeping")
    finally:
        root.handlers[:], root.level = saved
    shown = capsys.readouterr()
    assert shown.out == "" and shown.err == ""
    log = (tmp_path / "events.log").read_text()
    assert "Market data farm connection is OK" in log and "something worth keeping" in log
