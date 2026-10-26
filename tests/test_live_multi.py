"""Several (strategy, symbol) pairs in one process over one fake IB: state, limits, isolation, reconnect, STOP, logs."""

import logging

import pytest

from live.alerts import Alerter
from live.daily_log import engine_dirs, setup_multi_logging
from live.engines import Pair, load_pairs
from live.feed import BarFeed
from live.multi import MultiRunner
from live.risk import Risk, load_limits, stop_path
from live.runner import Link, Runner
from live.state import LiveState, save, settings_key
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_alerts import TOPIC, Capture
from tests.test_live_runner import BARS, ENTRY, Script, at, lines

PAIRS = [Pair("opening_range_breakout", "QQQ", {}), Pair("donchian_breakout", "SPY", {})]
LIMITS = """
symbols:
  QQQ: {max_shares: 10, max_position_value: 8000}
  SPY: {max_shares: 5, max_position_value: 8000}
max_trades_per_day: 4
daily_loss_limit: 500
"""


def world(tmp_path, scripts=None, *, now=f"{DAY1} 10:12", limits=LIMITS, reconnect=None, pairs=PAIRS, capture=None):
    """Two runners on ONE fake IB, each with a state that has stepped up to the 10:00 bar. Returns (ib, multi, runners)."""
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.market_price = 100.0
    clock = [at(now)]
    risk = Risk(load_limits(limits), root=tmp_path)
    alerter = Alerter(TOPIC, post=capture, background=False) if capture else None
    link, runners = Link(), []
    for p in pairs:
        feed = BarFeed(ib, None, clock=lambda: clock[0], fetch=lambda *a: BARS, settle_min=0)
        r = Runner(ib, p.strategy, p.symbol, {}, strategy=Script((scripts or {}).get(p.symbol, {})), root=tmp_path,
                   clock=lambda: clock[0], feed=feed, risk=risk, alerter=alerter, link=link, reconnect=reconnect,
                   reconnect_wait_s=0)
        save(r.state_path, LiveState(p.strategy, p.symbol, settings_key(p.strategy, p.symbol, {}),
                                     last_bar_ts=at(f"{DAY1} 10:00").isoformat()))
        runners.append(r)
    return ib, MultiRunner(runners), runners


def test_two_pairs_trade_over_one_connection_each_with_its_own_state_and_orders(tmp_path):
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}, "SPY": {"10:05": ENTRY}})
    assert qqq.ib is spy.ib is ib and qqq.executor.ib is spy.executor.ib
    multi.run(once=True)
    refs = {o.orderRef for _, o in ib.placed}
    assert any(r.startswith("live:opening_range_breakout:QQQ:") for r in refs)
    assert any(r.startswith("live:donchian_breakout:SPY:") for r in refs)
    assert qqq.state_path != spy.state_path and qqq.dir != spy.dir
    assert qqq.state.position.shares == 10 and spy.state.position.shares == 5  # each capped by ITS limits
    assert ib.holdings == {"QQQ": 10, "SPY": 5}
    assert lines(qqq, "signals.jsonl")[0]["action"] == "bought" and lines(spy, "signals.jsonl")[0]["action"] == "bought"


def test_a_symbol_without_limits_is_refused_for_that_pair_only(tmp_path):
    only_qqq = "symbols:\n  QQQ: {max_shares: 10, max_position_value: 8000}\nmax_trades_per_day: 4\ndaily_loss_limit: 500\n"
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}, "SPY": {"10:05": ENTRY}}, limits=only_qqq)
    multi.run(once=True)
    assert ib.holdings.get("QQQ") == 10 and not ib.holdings.get("SPY")
    assert "risk" in lines(spy, "signals.jsonl")[0]["action"] and spy.state.halted is None


def test_the_account_wide_trade_limit_counts_both_pairs(tmp_path):
    two = LIMITS.replace("max_trades_per_day: 4", "max_trades_per_day: 1")
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}, "SPY": {"10:05": ENTRY}}, limits=two)
    multi.run(once=True)
    assert ib.holdings.get("QQQ") == 10 and not ib.holdings.get("SPY")  # the second entry of the day is refused
    assert "max_trades_per_day" in lines(spy, "signals.jsonl")[0]["action"]


def test_one_pair_halting_does_not_stop_the_other(tmp_path):
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}})
    ib.holdings["SPY"] = 3  # IB holds SPY shares the SPY state cannot explain
    multi.run(once=True)
    assert spy.state.halted and "SPY" in spy.state.halted
    assert qqq.state.halted is None and qqq.state.position is not None
    assert not any(o.orderRef.startswith("live:donchian_breakout") for _, o in ib.placed)


def test_the_loop_ends_when_every_pair_has_halted(tmp_path):
    ib, multi, runners = world(tmp_path)
    ib.holdings.update(QQQ=1, SPY=1)
    sleeps = []
    ib.sleep = lambda s: sleeps.append(s)
    multi.run()  # no once=True: it must end by itself
    assert all(r.state.halted for r in runners) and sleeps == []  # nothing left to poll: no pause either


def test_an_unexpected_error_in_one_pair_halts_that_pair_and_the_other_goes_on(tmp_path):
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}, "SPY": {"10:05": ENTRY}})
    qqq.start, qqq.tick = (lambda: None), (lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    qqq.state = LiveState("opening_range_breakout", "QQQ", "k")
    multi.run(once=True)
    assert "boom" in qqq.state.halted and spy.state.position is not None


def test_two_engines_on_one_symbol_or_two_connections_are_refused(tmp_path):
    ib, multi, (qqq, spy) = world(tmp_path)
    spy.symbol = "QQQ"
    with pytest.raises(ValueError, match="same symbol"):
        MultiRunner([qqq, spy])
    spy.symbol, spy.ib = "SPY", FakeIB()
    with pytest.raises(ValueError, match="one IB connection"):
        MultiRunner([qqq, spy])


# -- a dropped connection: every pair re-checks, not only the one that reconnected -----------------------------------------------

def test_after_a_reconnect_every_pair_reconciles_against_ib(tmp_path):
    box = {}

    def reconnect():
        box["ib"].connected = True
        box["ib"].holdings["SPY"] = 7  # something happened to SPY while we were away

    cap = Capture()
    ib, multi, (qqq, spy) = world(tmp_path, reconnect=reconnect, capture=cap)
    box["ib"] = ib
    multi.run(once=True)  # starts clean ...
    ib.holdings["SPY"] = 0
    ib.connected = False  # ... then the Gateway drops
    multi.run(once=True)  # (a new run re-starts both; the drop is then seen by the first tick)
    assert qqq.state.halted is None
    assert spy.state.halted and "SPY" in spy.state.halted and "reconcile after reconnect" in spy.state.halted
    assert cap.kinds().count("halt") == 1 and "connection" in cap.kinds()
    lost = [t for t in cap.texts() if t.startswith("Lost")]
    assert len(lost) == 1  # ONE 'connection lost' for the whole process, not one per pair


# -- STOP -----------------------------------------------------------------------------------------------------------------------------

def test_the_stop_file_stops_every_pair_and_alerts_once(tmp_path):
    cap = Capture()
    ib, multi, runners = world(tmp_path, capture=cap)
    ib.sleep = lambda s: stop_path(tmp_path).write_text("kill switch\n")
    multi.run()  # the first pause drops the STOP file; the next loop sees it
    assert all(r.stopped for r in runners) and all(r.state.halted is None for r in runners)
    assert cap.kinds() == ["kill"]


def test_a_stop_file_at_start_keeps_every_pair_from_trading(tmp_path):
    ib, multi, runners = world(tmp_path, {"QQQ": {"10:05": ENTRY}, "SPY": {"10:05": ENTRY}})
    stop_path(tmp_path).write_text("kill switch\n")
    multi.run(once=True)
    assert ib.placed == [] and all(r.stopped for r in runners)


# -- one log file per pair and day ------------------------------------------------------------------------------------------------

@pytest.fixture
def log_handlers():
    added = []
    yield added
    for logger, handler in added:
        logger.removeHandler(handler)
        handler.close()


def test_each_pair_has_its_own_daily_log_file(tmp_path, log_handlers):
    day = ["2026-03-02"]
    log_handlers += setup_multi_logging(engine_dirs(PAIRS, tmp_path), tmp_path, today=lambda: day[0])
    ib, multi, (qqq, spy) = world(tmp_path, {"QQQ": {"10:05": ENTRY}})
    multi.run(once=True)
    q, s = qqq.dir / "logs" / "2026-03-02.log", spy.dir / "logs" / "2026-03-02.log"
    assert q.exists() and s.exists()
    qtext, stext = q.read_text(), s.read_text()
    assert "on QQQ" in qtext and "on SPY" not in qtext and "ENTRY signal" in qtext
    assert "on SPY" in stext and "on QQQ" not in stext and "ENTRY signal" not in stext
    assert "bought" in qtext and "QQQ" not in stext


def test_a_new_day_starts_a_new_file(tmp_path, log_handlers):
    day = ["2026-03-02"]
    log_handlers += setup_multi_logging(engine_dirs(PAIRS, tmp_path), tmp_path, today=lambda: day[0])
    ib, multi, (qqq, spy) = world(tmp_path)
    multi.run(once=True)
    day[0] = "2026-03-03"
    multi.run(once=True)
    names = sorted(p.name for p in (qqq.dir / "logs").iterdir())
    assert names == ["2026-03-02.log", "2026-03-03.log"]
    first = (qqq.dir / "logs" / "2026-03-02.log").read_text()
    assert first.count("reconcile :") == 1  # the second start went to the second file


def test_records_outside_any_pair_go_to_the_engine_file(tmp_path, log_handlers):
    log_handlers += setup_multi_logging(engine_dirs(PAIRS, tmp_path), tmp_path, today=lambda: "2026-03-02")
    logging.getLogger("live").warning("something about the whole process")
    text = (tmp_path / "engine" / "logs" / "2026-03-02.log").read_text()
    assert "something about the whole process" in text
    assert not list(tmp_path.glob("*_*/logs/*.log"))


# -- the engines file ---------------------------------------------------------------------------------------------------------------------

def write(tmp_path, text):
    path = tmp_path / "engines.yaml"
    path.write_text(text)
    return path


def test_the_shipped_engines_file_loads_and_every_symbol_has_limits():
    pairs = load_pairs(risk=Risk.from_file())
    assert [(p.strategy, p.symbol) for p in pairs] == [("opening_range_breakout", "QQQ")]  # no SPY: the desk's forward test owns it
    assert pairs[0].settings == {"range_minutes": 15}


def test_times_in_settings_are_parsed_when_quoted(tmp_path):
    path = write(tmp_path, "engines:\n  - {strategy: ema_trend, symbol: SPY, settings: {no_entry_from: '11:30', no_entry_until: '14:00'}}\n")
    (pair,) = load_pairs(path)
    assert str(pair.settings["no_entry_from"]) == "11:30:00"


@pytest.mark.parametrize("text,message", [
    ("engines: []\n", "non-empty"),
    ("nothing: 1\n", "non-empty"),
    ("engines:\n  - {strategy: nope, symbol: QQQ}\n", "unknown strategy"),
    ("engines:\n  - {strategy: ema_trend}\n", "needs strategy and symbol"),
    ("engines:\n  - {strategy: ema_trend, symbol: QQQ, color: red}\n", "needs strategy and symbol"),
    ("engines:\n  - {strategy: ema_trend, symbol: QQQ, settings: {bogus: 1}}\n", "bogus"),
    ("engines:\n  - {strategy: ema_trend, symbol: QQQ}\n  - {strategy: donchian_breakout, symbol: qqq}\n", "one engine per symbol"),
    ("engines:\n  - {strategy: ema_trend, symbol: IWM}\n", "no limits for IWM"),
])
def test_a_bad_engines_file_is_refused_with_a_reason(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_pairs(write(tmp_path, text), risk=Risk(load_limits(LIMITS), root=tmp_path))
