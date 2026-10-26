"""The kill switch and the STOP file."""

from types import SimpleNamespace

import pytest
from ib_async import LimitOrder, StopOrder

from live.broker import Broker
from live.kill import kill, main_precheck
from live.risk import stop_path
from live.runner import Runner
from live.state import StopFileError
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_runner import Script, at, make

SPY, QQQ = SimpleNamespace(symbol="SPY"), SimpleNamespace(symbol="QQQ")


def world(tmp_path):
    """A paper account holding 3 SPY with our stop and target resting, plus someone else's order on SPY."""
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.market_price = 100.0
    ib.holdings["SPY"] = 3
    ours = Broker(ib, "live:ema_trend:SPY")
    ours.place(SPY, StopOrder("SELL", 3, 90.0, tif="GTC"), ref="live:ema_trend:SPY:2026-03-02:1000")
    ours.place(SPY, LimitOrder("SELL", 3, 120.0, tif="GTC"), ref="live:ema_trend:SPY:2026-03-02:1000")
    ib.placeOrder(SPY, SimpleNamespace(orderId=0, orderRef="by-hand", action="BUY", orderType="LMT", totalQuantity=1,
                                       lmtPrice=50.0, transmit=True))
    return ib


def run(ib, tmp_path, **kw):
    out = []
    code = kill(ib, root=tmp_path, price=lambda symbol: 100.0, out=out.append, **kw)
    return code, "\n".join(out)


def test_kill_cancels_our_orders_sells_our_position_and_writes_stop(tmp_path):
    ib = world(tmp_path)
    code, text = run(ib, tmp_path)
    assert code == 0, text
    assert ib.holdings["SPY"] == 0
    still_open = [t.order.orderRef for t in ib.openTrades()]
    assert still_open == ["by-hand"]  # ours are gone, a foreign order is not ours to cancel
    assert stop_path(tmp_path).exists() and "kill switch" in stop_path(tmp_path).read_text()
    assert "flat" in text and "STOP" in text


def test_kill_writes_stop_first_so_nothing_new_can_start_while_it_works(tmp_path):
    ib = world(tmp_path)
    seen = []
    real = ib.cancelOrder
    ib.cancelOrder = lambda o: (seen.append(stop_path(tmp_path).exists()), real(o))[1]
    run(ib, tmp_path)
    assert seen and all(seen)


def test_kill_twice_is_harmless(tmp_path):
    ib = world(tmp_path)
    run(ib, tmp_path)
    placed = len(ib.placed)
    code, text = run(ib, tmp_path)
    assert code == 0 and len(ib.placed) == placed and "nothing to cancel" in text


def test_kill_never_touches_the_desks_orders_or_its_spy_position(tmp_path):
    """The desk shares the account and its refs also start with "live:" (live:desk:, live:mcp:, live:open:)."""
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.market_price = 100.0
    ib.holdings["SPY"] = 2  # the desk's share
    for tag in ("live:desk:SPY", "live:mcp:SPY", "live:open:SPY"):
        Broker(ib, tag).place(SPY, StopOrder("SELL", 2, 90.0, tif="GTC"), ref=f"{tag}:2026-03-02:1000")
    code, text = run(ib, tmp_path)
    assert ib.holdings["SPY"] == 2 and len(ib.openTrades()) == 3, text


def test_kill_only_sells_symbols_that_are_ours(tmp_path):
    ib = world(tmp_path)
    ib.holdings["QQQ"] = 7  # someone's own holding: no order of ours, no state file
    run(ib, tmp_path)
    assert ib.holdings["QQQ"] == 7 and ib.holdings["SPY"] == 0


def test_a_symbol_with_a_runner_state_file_counts_as_ours_even_without_orders(tmp_path):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.market_price = 100.0
    ib.holdings["QQQ"] = 5
    (tmp_path / "ema_trend_QQQ").mkdir()
    (tmp_path / "ema_trend_QQQ" / "state.json").write_text('{"symbol": "QQQ"}')
    code, _ = run(ib, tmp_path)
    assert code == 0 and ib.holdings["QQQ"] == 0


def test_kill_reports_failure_when_a_position_cannot_be_closed(tmp_path):
    ib = world(tmp_path)
    ib.freeze = True  # nothing fills
    code, text = run(ib, tmp_path)
    assert code == 1 and "NOT FLAT" in text and stop_path(tmp_path).exists()


def test_kill_reports_failure_when_a_cancel_is_not_confirmed(tmp_path):
    ib = world(tmp_path)
    ib.slow_cancel = True
    code, text = run(ib, tmp_path)
    assert code == 1 and "not confirmed" in text


def test_clear_removes_the_stop_file(tmp_path):
    stop_path(tmp_path).write_text("x")
    from live.kill import clear
    clear(tmp_path)
    assert not stop_path(tmp_path).exists()


# -- the runner honours STOP ------------------------------------------------------------------

def test_runner_refuses_to_start_while_stop_exists(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00")
    stop_path(tmp_path).write_text("kill switch")
    with pytest.raises(StopFileError, match="STOP"):
        runner.start()
    assert ib.placed == []


def test_runner_stops_looping_when_stop_appears_and_trades_nothing_more(tmp_path):
    from tests.test_live_runner import ENTRY
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00")
    runner.start()
    stop_path(tmp_path).write_text("kill switch")
    runner.tick()
    assert ib.placed == [] and runner.stopped
    assert runner.state.halted is None  # removing STOP is enough to resume; no --clear-halt needed


def test_run_loop_exits_on_stop(tmp_path):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00")
    ticks = []
    real = runner.tick
    def tick():
        ticks.append(1)
        real()
        stop_path(tmp_path).write_text("kill switch")
    runner.tick = tick
    runner.run()
    assert len(ticks) == 2 and runner.stopped and ib.placed == []  # the second loop saw STOP, did nothing and left


def test_the_cli_precheck_refuses_before_connecting(tmp_path):
    stop_path(tmp_path).write_text("kill switch")
    assert main_precheck(tmp_path) is not None
    stop_path(tmp_path).unlink()
    assert main_precheck(tmp_path) is None
