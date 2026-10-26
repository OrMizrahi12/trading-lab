"""Risk limits outside the strategy: read from YAML, checked in Broker before every order leaves the program."""

import json
from types import SimpleNamespace

import pytest
import yaml
from ib_async import LimitOrder, StopOrder

from live.broker import Broker
from live.execution import Executor
from live.risk import RiskLimits, RiskRefused, Risk, load_limits, stop_path
from strategies.base import Entry
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_runner import ENTRY, Script, at, lines, make as make_runner

TAG = "live:ema_trend:SPY"
SPY = SimpleNamespace(symbol="SPY")
YAML = """
symbols:
  SPY: {max_shares: 10, max_position_value: 8000}
max_trades_per_day: 2
daily_loss_limit: 500
"""


def limits(text=YAML, tmp_path=None):
    return load_limits(text)


def setup(tmp_path, text=YAML, equity=1_000_000.0, now=f"{DAY1} 15:00"):
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    ib.net_liquidation = equity
    ib.market_price = 100.0
    risk = Risk(load_limits(text), root=tmp_path, clock=lambda: at(now))
    return ib, Broker(ib, TAG, risk=risk), risk


def buy(qty=5, price=100.0):
    return LimitOrder("BUY", qty, price)


# -- the YAML ----------------------------------------------------------------------------

def test_yaml_is_read_into_limits():
    lim = load_limits(YAML)
    assert lim.symbols["SPY"].max_shares == 10 and lim.symbols["SPY"].max_position_value == 8000
    assert (lim.max_trades_per_day, lim.daily_loss_limit) == (2, 500)


@pytest.mark.parametrize("text", [
    "symbols: {SPY: {max_shares: 10}}\nmax_trades_per_day: 2\ndaily_loss_limit: 500",  # a symbol limit missing
    YAML + "extra: 1",  # an unknown key is a typo, not a setting
    YAML.replace("max_shares: 10", "max_shares: -1"),
    YAML.replace("daily_loss_limit: 500", "daily_loss_limit: 0"),
    "symbols: {}\nmax_trades_per_day: 2\ndaily_loss_limit: 500",
    "- not a mapping",
])
def test_a_bad_yaml_is_refused_not_guessed(text):
    with pytest.raises(ValueError):
        load_limits(text)


def test_the_shipped_limits_are_far_below_what_one_percent_risk_would_allow_on_a_million_dollar_account():
    path = __import__("live.risk", fromlist=["DEFAULT_LIMITS"]).DEFAULT_LIMITS
    lim = load_limits(path.read_text())
    assert {"SPY", "QQQ"} <= set(lim.symbols)
    for s in lim.symbols.values():
        assert s.max_position_value <= 10_000  # 1% of the account; 1% risk with a 1% stop would allow ~$1,000,000
    assert lim.daily_loss_limit <= 1_000 and lim.max_trades_per_day <= 5


# -- buys are checked --------------------------------------------------------------------

def test_a_buy_inside_the_limits_goes_out(tmp_path):
    ib, broker, _ = setup(tmp_path)
    broker.place(SPY, buy(5))
    assert len(ib.placed) == 1


def test_too_many_shares_refused_with_the_reason_logged(tmp_path, caplog):
    ib, broker, _ = setup(tmp_path)
    with pytest.raises(RiskRefused, match="max_shares"):
        broker.place(SPY, buy(11, 10.0))
    assert ib.placed == [] and broker.sent == 0
    assert "RISK REFUSED" in caplog.text and "max_shares" in caplog.text
    record = json.loads((tmp_path / "risk_refusals.jsonl").read_text().splitlines()[0])
    assert record["symbol"] == "SPY" and record["quantity"] == 11 and "max_shares" in record["reason"]


def test_position_value_is_held_plus_resting_buys_plus_this_order(tmp_path):
    ib, broker, _ = setup(tmp_path)
    ib.holdings["SPY"] = 4
    with pytest.raises(RiskRefused, match="max_position_value"):
        broker.place(SPY, buy(6, 1400.0))  # 10 shares are fine, but 10 x 1400 is 14000 > 8000
    broker.place(SPY, buy(2, 100.0))  # 4 held + 2: 600
    ib.freeze = True
    broker.place(SPY, buy(3, 100.0))  # rests at IB
    with pytest.raises(RiskRefused, match="max_shares"):
        broker.place(SPY, buy(2, 100.0))  # 4 + 2 (filled) + 3 resting + 2 = 11


def test_a_symbol_without_limits_cannot_be_bought(tmp_path):
    ib, broker, _ = setup(tmp_path)
    with pytest.raises(RiskRefused, match="no limits"):
        broker.place(SimpleNamespace(symbol="TSLA"), buy(1))


def test_a_buy_without_a_price_cannot_be_valued_and_is_refused(tmp_path):
    from ib_async import MarketOrder
    ib, broker, _ = setup(tmp_path)
    with pytest.raises(RiskRefused, match="limit price"):
        broker.place(SPY, MarketOrder("BUY", 1))


def test_max_trades_per_day_counts_todays_entries_at_ib(tmp_path):
    ib, broker, _ = setup(tmp_path)
    for minute in ("1000", "1005"):
        ib.holdings["SPY"] = 0
        broker.place(SPY, buy(1), ref=f"{TAG}:{DAY1}:{minute}")  # fills at once in the fake
    ib.holdings["SPY"] = 0
    with pytest.raises(RiskRefused, match="max_trades_per_day"):
        broker.place(SPY, buy(1), ref=f"{TAG}:{DAY1}:1010")


def test_daily_loss_limit_blocks_new_buys_from_the_first_equity_of_the_day(tmp_path):
    ib, broker, _ = setup(tmp_path, equity=1_000_000.0)
    broker.place(SPY, buy(1))  # first check of the day records 1,000,000
    ib.holdings["SPY"] = 0
    ib.net_liquidation = 999_600.0  # down 400: under the 500 limit
    broker.place(SPY, buy(1), ref=f"{TAG}:{DAY1}:1005")
    ib.holdings["SPY"] = 0
    ib.net_liquidation = 999_500.0  # down 500: at the limit
    with pytest.raises(RiskRefused, match="daily_loss_limit"):
        broker.place(SPY, buy(1), ref=f"{TAG}:{DAY1}:1010")


def test_the_loss_baseline_is_per_day(tmp_path):
    ib, broker, risk = setup(tmp_path, equity=1_000_000.0, now=f"{DAY1} 15:00")
    broker.place(SPY, buy(1))
    ib.holdings["SPY"] = 0
    ib.net_liquidation = 990_000.0  # a bad day...
    risk.clock = lambda: at("2026-03-03 15:00")  # ...and the next morning starts from where it is
    broker.place(SPY, buy(1), ref=f"{TAG}:2026-03-03:1000")


# -- sells and cancels are never refused ----------------------------------------------------

def test_sells_and_cancels_are_never_refused_even_with_every_limit_broken(tmp_path):
    ib, broker, risk = setup(tmp_path, equity=1.0)
    stop_path(tmp_path).write_text("stop")
    ib.holdings["SPY"] = 500
    broker.place(SPY, LimitOrder("SELL", 500, 99.0))
    broker.place(SPY, StopOrder("SELL", 500, 90.0))
    broker.place(SimpleNamespace(symbol="TSLA"), LimitOrder("SELL", 5, 99.0))
    broker.cancel(ib.placed[0][1])
    assert len(ib.placed) == 3 and len(ib.cancelled) == 1


def test_a_stop_file_refuses_every_buy(tmp_path):
    ib, broker, _ = setup(tmp_path)
    stop_path(tmp_path).write_text("kill switch")
    with pytest.raises(RiskRefused, match="STOP"):
        broker.place(SPY, buy(1))
    assert ib.placed == []


def test_without_risk_the_broker_behaves_as_before():
    ib = FakeIB()
    ib.connect("127.0.0.1", 4002)
    Broker(ib, TAG).place(SPY, buy(100000))
    assert len(ib.placed) == 1


# -- sizing stays inside the limits ---------------------------------------------------------

def test_size_is_clamped_to_the_limits_instead_of_asking_for_what_would_be_refused(tmp_path, caplog):
    ib, broker, _ = setup(tmp_path)
    ex = Executor(ib, broker, SPY)
    shares = ex.size(Entry(stop=99.0, target=float("inf")), 100.0)  # 1% risk on $1M would be ~6,450 shares
    assert shares == 10 and "clamped" in caplog.text
    ib.holdings["SPY"] = 4
    assert ex.size(Entry(stop=99.0, target=float("inf")), 100.0) == 6
    ib.holdings["SPY"] = 10
    assert ex.size(Entry(stop=99.0, target=float("inf")), 100.0) == 0


def test_position_value_clamps_size_too(tmp_path):
    ib, broker, _ = setup(tmp_path)
    ex = Executor(ib, broker, SPY)
    assert ex.size(Entry(stop=700.0, target=float("inf")), 760.0) == 10  # 8000 / (760 x 1.005) = 10
    assert ex.size(Entry(stop=1400.0, target=float("inf")), 1500.0) == 5  # 8000 / 1507.5


# -- the runner ----------------------------------------------------------------------------

def test_a_refused_entry_is_skipped_logged_and_does_not_halt_the_runner(tmp_path):
    risk = Risk(load_limits(YAML.replace("SPY", "NOPE")), root=tmp_path)
    ib, runner, _ = make_runner(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", risk=risk)
    runner.start()
    runner.tick()
    assert ib.placed == [] and not runner.state.halted and runner.state.pending_entry is None
    assert "risk" in lines(runner, "signals.jsonl")[0]["action"]
    assert (tmp_path / "risk_refusals.jsonl").exists()


def test_a_runner_with_limits_enters_small(tmp_path):
    risk = Risk(load_limits(YAML.replace("SPY", "QQQ")), root=tmp_path)
    ib, runner, _ = make_runner(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", risk=risk)
    ib.net_liquidation = 1_000_000.0
    runner.start()
    runner.tick()
    assert runner.state.position.shares == 10  # 1% risk on a million dollars would have been 6,000+


def test_the_desks_buys_do_not_count_against_our_trade_limit(tmp_path):
    from live.risk import is_ours

    assert is_ours("live:opening_range_breakout:QQQ:2026-10-26:0945") and is_ours("live:kill")
    assert not any(is_ours(r) for r in ("live:desk:SPY:2026-10-26:0945", "live:mcp:SPY", "live:open:XYZ", "by-hand", "", None))
