"""The trade journal (live/journal.py): signals, orders, fills with commission, snapshots, the engine hooks and the import."""

import sqlite3

import pytest
from ib_async import LimitOrder, Stock

from live.broker import PaperGuardError, connect_paper
from live.journal import Journal
from live.journal_import import import_from_ib
from strategies.base import Exit
from tests.fake_ib import FakeIB
from tests.test_live_runner import DAY1, DAY2, ENTRY, at, held, make


def rows(journal, sql, *args):
    journal.db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in journal.db.execute(sql, args).fetchall()]
    finally:
        journal.db.row_factory = None


@pytest.fixture
def journal(tmp_path):
    with Journal(tmp_path / "journal.sqlite3", clock=lambda: at(f"{DAY1} 10:12")) as j:
        yield j


def run_entry(tmp_path, journal, script=None, now="10:12"):
    ib, runner, clock = make(tmp_path, script or {"10:05": ENTRY}, f"{DAY1} {now}", last_bar="10:00", journal=journal)
    ib.now = at(f"{DAY1} {now}")
    runner.start()
    runner.tick()
    return ib, runner, clock


def test_schema_is_created_and_reopening_keeps_rows(tmp_path):
    path = tmp_path / "j.sqlite3"
    with Journal(path) as j:
        j.signal("live:x:QQQ", {"bar": "2026-01-05T15:00:00+00:00", "kind": "entry", "action": "bought"})
    with Journal(path) as j:
        assert j.db.execute("SELECT count(*) FROM signals").fetchone()[0] == 1
        names = {r[0] for r in j.db.execute("SELECT name FROM sqlite_master")}
    assert {"signals", "orders", "fills", "snapshots", "entry_pnl"} <= names


def test_an_entry_writes_signal_orders_and_fills_with_commission(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    ref = f"live:ema_trend:QQQ:{DAY1}:1005"
    sig = rows(journal, "SELECT * FROM signals")
    assert [(s["kind"], s["action"], s["entry_ref"], s["dry_run"]) for s in sig] == [("entry", "bought", ref, 0)]
    assert sig[0]["stop"] == 99.0 and sig[0]["shares"] == runner.state.position.shares
    orders = rows(journal, "SELECT action, order_type, limit_price, stop_price, order_ref, status FROM orders ORDER BY order_id")
    assert [(o["action"], o["order_type"], o["order_ref"]) for o in orders] == [("BUY", "LMT", ref), ("SELL", "STP", ref)]
    assert orders[0]["status"] == "Filled" and orders[1]["stop_price"] == 99.0
    fill = rows(journal, "SELECT * FROM fills")
    assert len(fill) == 1 and fill[0]["side"] == "BOT" and fill[0]["entry_ref"] == ref
    assert fill[0]["commission"] == max(fill[0]["shares"] * 0.005, 1.0) and fill[0]["commission_currency"] == "USD"
    assert fill[0]["time_utc"] == f"{DAY1} 15:12:00" and fill[0]["day"] == DAY1


def test_syncing_again_adds_nothing(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    counts = journal.sync(ib, full=True, completed=True)
    assert counts["new_orders"] == 0 and counts["new_fills"] == 0
    assert len(rows(journal, "SELECT 1 FROM fills")) == 1 and len(rows(journal, "SELECT 1 FROM orders")) == 2


def test_a_late_commission_fills_in_the_null_without_a_second_row(tmp_path, journal):
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", journal=journal)
    ib.delay_commissions = True
    runner.start()
    runner.tick()
    assert rows(journal, "SELECT commission FROM fills") == [{"commission": None}]  # NULL, not a made-up 0
    assert rows(journal, "SELECT fills_without_commission FROM entry_pnl")[0]["fills_without_commission"] == 1
    ib.release_commissions()
    runner.tick()
    got = rows(journal, "SELECT commission FROM fills")
    assert len(got) == 1 and got[0]["commission"] >= 1.0


def test_pnl_per_entry_ref_covers_the_stop(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    shares = runner.state.position.shares
    stop = next(t for t in ib.trades() if t.order.orderType == "STP")
    ib.now = at(f"{DAY1} 10:40")
    ib.trigger(stop, 99.0)  # the stop carries the entry ref itself
    journal.sync(ib)
    ref = f"live:ema_trend:QQQ:{DAY1}:1005"
    pnl = rows(journal, "SELECT * FROM entry_pnl")
    assert len(pnl) == 1 and pnl[0]["entry_ref"] == ref and pnl[0]["status"] == "closed"
    assert pnl[0]["bought"] == pnl[0]["sold"] == shares
    commission = 2 * max(shares * 0.005, 1.0)
    assert pnl[0]["gross_pnl"] == pytest.approx(shares * (99.0 - 100.0))  # filled at the fake's 100.0, stopped at 99
    assert pnl[0]["commission"] == pytest.approx(commission)
    assert pnl[0]["net_pnl"] == pytest.approx(shares * -1.0 - commission)


def test_a_sell_with_the_plain_tag_is_tied_to_the_days_entry(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    shares = runner.state.position.shares
    sell = LimitOrder("SELL", shares, 99.5, tif="DAY")
    sell.orderRef = "live:ema_trend:QQQ"  # what Executor.exit sends: the tag without a session or bar
    ib.now = at(f"{DAY1} 11:00")
    trade = ib.placeOrder(Stock("QQQ"), sell)
    assert trade.orderStatus.status == "Filled"
    journal.sync(ib)
    fills = rows(journal, "SELECT side, entry_ref FROM fills ORDER BY time_utc")
    assert [f["side"] for f in fills] == ["BOT", "SLD"] and fills[0]["entry_ref"] == fills[1]["entry_ref"] is not None


def test_an_exit_signal_goes_to_the_journal_and_its_sell_has_no_entry_when_none_was_bought_today(tmp_path, journal):
    ib, runner, _ = make(tmp_path, {"10:30": Exit(reason="out")}, f"{DAY1} 10:42", last_bar="10:25", position=held(),
                         holdings=10, journal=journal)
    ib.now = at(f"{DAY1} 10:42")
    runner.start()
    runner.tick()
    assert rows(journal, "SELECT kind, reason, action, entry_ref FROM signals") == [
        {"kind": "exit", "reason": "out", "action": "sold", "entry_ref": None}]
    assert rows(journal, "SELECT side, shares, order_ref, entry_ref FROM fills") == [
        {"side": "SLD", "shares": 10, "order_ref": "live:ema_trend:QQQ", "entry_ref": None}]


def test_dry_run_records_signals_flagged_and_no_orders_or_fills(tmp_path, journal):
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", dry_run=True, journal=journal)
    runner.start()
    runner.tick()
    assert rows(journal, "SELECT action, dry_run FROM signals") == [{"action": "dry run", "dry_run": 1}]
    assert rows(journal, "SELECT 1 FROM orders") == [] and rows(journal, "SELECT 1 FROM fills") == []


def test_a_broken_journal_never_stops_trading(tmp_path, journal, monkeypatch):
    def boom(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(journal, "sync", boom)
    monkeypatch.setattr(journal, "signal", boom)
    ib, runner, _ = run_entry(tmp_path, journal)
    assert [o.action for _, o in ib.placed] == ["BUY", "SELL"] and runner.state.position is not None
    assert not runner.state.halted


def test_the_engine_copies_only_its_own_orders_but_the_import_takes_all(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    other = LimitOrder("BUY", 3, 150.0)  # crosses the fake market at 100, so it fills
    other.orderRef = "someone else"
    ib.now = at(f"{DAY1} 10:20")
    ib.placeOrder(Stock("SPY"), other)
    runner.tick()
    assert {r["symbol"] for r in rows(journal, "SELECT symbol FROM fills")} == {"QQQ"}
    counts = import_from_ib(ib, journal)
    assert {r["symbol"] for r in rows(journal, "SELECT symbol FROM fills")} == {"QQQ", "SPY"}
    assert counts["snapshot"] and counts["new_fills"] == 1
    assert rows(journal, "SELECT entry_ref FROM fills WHERE symbol = 'SPY'") == [{"entry_ref": None}]
    assert rows(journal, "SELECT entry_ref, status FROM entry_pnl WHERE symbol = 'SPY'") == [
        {"entry_ref": "(not ours)", "status": "open"}]


def test_a_restarted_session_gets_the_finished_orders_from_ib(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    fresh = Journal(tmp_path / "fresh.sqlite3", clock=lambda: at(f"{DAY1} 10:30"))
    ib.restart()  # the new session no longer lists done orders in trades()
    counts = fresh.sync(ib, full=True, completed=True)
    assert counts["new_orders"] >= 1 and counts["new_fills"] == 1
    fresh.close()


def test_an_order_first_seen_without_a_perm_id_is_not_doubled_when_it_gets_one(journal):
    ib = FakeIB()
    order = LimitOrder("BUY", 5, 100.0)
    order.orderRef, order.clientId = "live:x:QQQ", 6
    ib.placeOrder(Stock("QQQ"), order)
    journal.sync(ib)
    order.permId = 987654
    journal.sync(ib)
    got = rows(journal, "SELECT perm_id FROM orders")
    assert got == [{"perm_id": 987654}]


def test_snapshot_keeps_the_first_reading_of_the_day_and_one_row_per_day(journal):
    ib = FakeIB()
    ib.net_liquidation = 1_000_000.0
    assert journal.snapshot(ib, now=at(f"{DAY1} 09:30"))
    ib.net_liquidation = 1_000_250.5
    journal.snapshot(ib, now=at(f"{DAY1} 16:00"))
    ib.net_liquidation = 999_000.0
    journal.snapshot(ib, now=at(f"{DAY2} 09:30"))
    got = rows(journal, "SELECT day, open_net_liquidation, net_liquidation FROM snapshots ORDER BY day")
    assert got == [{"day": DAY1, "open_net_liquidation": 1_000_000.0, "net_liquidation": 1_000_250.5},
                   {"day": DAY2, "open_net_liquidation": 999_000.0, "net_liquidation": 999_000.0}]


def test_import_connection_is_read_only_on_its_own_client_id_and_refuses_a_bad_account_or_port():
    ib = connect_paper(lambda: FakeIB(), port=4002, client_id=7, readonly=True)
    assert ib.connect_args[:3] == ("127.0.0.1", 4002, 7) and ib.connect_args[3] == {"readonly": True}
    with pytest.raises(PaperGuardError):
        connect_paper(lambda: FakeIB(accounts=("U7654321",)), port=4002, client_id=7, readonly=True)
    fake = FakeIB()
    with pytest.raises(PaperGuardError):
        connect_paper(lambda: fake, port=80, client_id=7, readonly=True)
    assert fake.connect_args is None


def test_a_kill_switch_sell_is_tied_to_the_open_entry_of_its_symbol(tmp_path, journal):
    ib, runner, _ = run_entry(tmp_path, journal)
    sell = LimitOrder("SELL", runner.state.position.shares, 99.5, tif="DAY")
    sell.orderRef = "live:kill"
    ib.now = at(f"{DAY1} 11:00")
    ib.placeOrder(Stock("QQQ"), sell)
    unrelated = LimitOrder("SELL", 1, 99.5, tif="DAY")
    unrelated.orderRef = "live:kill"
    ib.placeOrder(Stock("SPY"), unrelated)  # no entry of ours in SPY: stays unattributed
    journal.sync(ib)
    got = rows(journal, "SELECT symbol, side, entry_ref FROM fills WHERE side = 'SLD' ORDER BY symbol")
    assert got == [{"symbol": "QQQ", "side": "SLD", "entry_ref": f"live:ema_trend:QQQ:{DAY1}:1005"},
                   {"symbol": "SPY", "side": "SLD", "entry_ref": None}]


def test_waiting_for_commissions_ends_even_if_they_never_come(journal):
    ib = FakeIB()
    ib.delay_commissions = True
    ib.market_price = 100.0
    ib.now = at(f"{DAY1} 10:00")
    ib.placeOrder(Stock("QQQ"), LimitOrder("BUY", 2, 101.0))
    journal.sync(ib, full=True, commission_wait_s=2)
    assert rows(journal, "SELECT commission FROM fills") == [{"commission": None}]
