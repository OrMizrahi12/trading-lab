"""Offline tests for backtests/portfolio.py on tiny hand-made bars and scripted strategies."""

import pandas as pd

from backtests.engine import CostModel, run_backtest
from backtests.portfolio import Sleeve, run_portfolio
from strategies.base import Entry, Strategy
from tests.test_engine import FLAT, ZERO, Scripted, make_bars

USUAL = CostModel()


def sleeves(*scripts, rows=None):
    rows = rows or [FLAT] * 8
    return [Sleeve(f"s{k}", make_bars(rows), Scripted(s)) for k, s in enumerate(scripts)]


def test_one_sleeve_without_rules_matches_the_single_strategy_engine():
    rows = [FLAT, FLAT, (100.5, 101, 100.2, 100.8), (100.8, 102, 100.6, 101.5),
            (101.5, 101.6, 98, 99), FLAT, FLAT, FLAT]
    script = {1: Entry(stop=99.5, target=105), 5: Entry(stop=98, target=103)}
    solo = run_backtest(make_bars(rows), Scripted(script), costs=USUAL)
    port = run_portfolio([Sleeve("a", make_bars(rows), Scripted(script))], costs=USUAL)
    cols = ["entry_time", "exit_time", "entry_price", "exit_price", "shares", "reason",
            "net_pnl", "r_multiple"]
    pd.testing.assert_frame_equal(port.trades[cols], solo.trades[cols])
    pd.testing.assert_series_equal(port.equity, solo.equity)


def test_trade_cap_refuses_the_later_sleeve():
    port = run_portfolio(sleeves({1: Entry(stop=99, target=110)}, {1: Entry(stop=99, target=110)}),
                         costs=ZERO, max_trades_per_day=1)
    assert list(port.trades["sleeve"]) == ["s0"]
    assert port.blocked["trade_cap"] == 1


def test_daily_loss_limit_blocks_later_entries_but_not_without_it():
    # s0 is stopped through a big gap (a loss well past 2%); s1 only signals afterwards.
    rows = [FLAT, FLAT, FLAT, (80, 80.5, 79.5, 80), (80, 80.1, 79.9, 80), (80, 80.1, 79.9, 80),
            (80, 80.1, 79.9, 80), (80, 80.1, 79.9, 80)]
    scripts = ({1: Entry(stop=99, target=200)}, {3: Entry(stop=79, target=200)})
    limited = run_portfolio(sleeves(*scripts, rows=rows), costs=ZERO, daily_loss_limit=0.02)
    free = run_portfolio(sleeves(*scripts, rows=rows), costs=ZERO)
    assert list(limited.trades["sleeve"]) == ["s0"]
    assert limited.blocked["daily_loss_limit"] == 1 and limited.limit_days == 1
    assert sorted(free.trades["sleeve"]) == ["s0", "s1"]


def test_sleeves_share_one_pool_of_cash():
    # A one-cent stop makes the risk rule ask for far more than the account can buy: the first
    # sleeve takes all the cash, the second has none left.
    port = run_portfolio(sleeves({1: Entry(stop=99.99, target=110)}, {1: Entry(stop=99.99, target=110)}),
                         costs=ZERO)
    assert list(port.trades["sleeve"]) == ["s0"]
    assert port.blocked["no_cash"] == 1
    assert (port.trades["shares"] * port.trades["entry_price"]).max() <= 100_000
