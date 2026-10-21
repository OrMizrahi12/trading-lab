"""Offline tests for backtests/engine.py on tiny hand-made bars and a scripted strategy.

Bars are (open, high, low, close) rows, 5 minutes apart from 09:30 New York time on
one day. `Scripted` returns a fixed intent when the bar with a given index has closed.
"""

import pandas as pd
import pytest

from backtests.engine import CostModel, run_backtest
from strategies.base import Entry, Exit, Strategy

ZERO = CostModel(0.0, 0.0, 0.0)


def make_bars(rows, day="2026-03-02", start="09:30") -> pd.DataFrame:
    idx = pd.date_range(f"{day} {start}", periods=len(rows), freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, index=idx.tz_convert("UTC"),
                        columns=["open", "high", "low", "close"]).assign(volume=1000)


class Scripted(Strategy):
    """Returns script[i] once bar i has closed, and remembers how many bars it was shown."""

    def __init__(self, script):
        self.script, self.seen = script, []

    def on_bar(self, bars, position):
        self.seen.append(len(bars))
        return self.script.get(len(bars) - 1)  # only valid with lookback_bars=None


def run(rows, script, costs=ZERO, **kw):
    kw.setdefault("lookback_bars", None)
    return run_backtest(make_bars(rows), Scripted(script), costs=costs, **kw)


FLAT = (100, 100.1, 99.9, 100)


def test_entry_fills_at_next_open_not_signal_close():
    rows = [FLAT, FLAT, (100.5, 100.6, 100.4, 100.5), (100.5, 100.6, 100.4, 100.5)]
    t = run(rows, {1: Entry(stop=99, target=110)}).trades.iloc[0]
    assert t["entry_price"] == 100.5  # bar 2's open; bar 1's close was 100
    assert t["entry_time"] == make_bars(rows).index[2]


def test_stop_wins_when_one_bar_touches_both():
    rows = [FLAT, FLAT, (100, 103, 98, 100), FLAT]  # bar 2 reaches target 102 and stop 99
    t = run(rows, {1: Entry(stop=99, target=102)}).trades.iloc[0]
    assert t["reason"] == "stop" and t["exit_price"] == 99


def test_gap_through_stop_fills_at_open():
    rows = [FLAT, FLAT, FLAT, (97, 97.5, 96.5, 97), FLAT]
    t = run(rows, {1: Entry(stop=99, target=110)}).trades.iloc[0]
    assert t["reason"] == "stop" and t["exit_price"] == 97  # not 99


def test_gap_through_target_fills_at_open():
    rows = [FLAT, FLAT, FLAT, (105, 105.5, 104.5, 105), FLAT]
    t = run(rows, {1: Entry(stop=99, target=102)}).trades.iloc[0]
    assert t["reason"] == "target" and t["exit_price"] == 105


def test_stop_can_hit_on_the_entry_bar():
    rows = [FLAT, FLAT, (100, 100.2, 98.5, 99), FLAT]
    t = run(rows, {1: Entry(stop=99, target=110)}).trades.iloc[0]
    assert t["entry_time"] == t["exit_time"] and t["reason"] == "stop"


def test_exit_intent_fills_at_next_open():
    rows = [FLAT, FLAT, FLAT, (101, 101.2, 100.8, 101), (102, 102.2, 101.8, 102)]
    t = run(rows, {1: Entry(stop=95, target=110), 3: Exit("time")}).trades.iloc[0]
    assert t["reason"] == "time" and t["exit_price"] == 102


def test_stop_out_costs_one_percent_of_equity():
    rows = [FLAT, FLAT, FLAT, (99.5, 99.6, 98.8, 99)]  # dips through the 99 stop, no gap
    r = run(rows, {1: Entry(stop=99, target=110)})
    t = r.trades.iloc[0]
    assert t["shares"] == 1000  # 1% of 100k = 1000 dollars / 1 dollar risk per share
    assert t["net_pnl"] == pytest.approx(-1000)
    assert t["r_multiple"] == pytest.approx(-1)


def test_stop_out_with_costs_is_still_about_one_percent():
    rows = [FLAT, FLAT, FLAT, (99.5, 99.6, 98.8, 99)]
    r = run(rows, {1: Entry(stop=99, target=110)}, costs=CostModel(0.01, 0.01, 0.01))
    t = r.trades.iloc[0]
    one_share = 99 + 0.05  # worst-case rounding slack is under one share of risk
    assert -1000 - one_share < t["net_pnl"] <= -1000 + one_share
    assert t["r_multiple"] == pytest.approx(-1, abs=0.01)


def test_cost_arithmetic_on_a_known_round_trip():
    # Buy open 100: pay 100 + 0.005 + 0.01. Stop 99 hit: receive 99 - 0.005 - 0.01.
    rows = [FLAT, FLAT, FLAT, (99.5, 99.6, 98.8, 99)]
    r = run(rows, {1: Entry(stop=99, target=110)}, costs=CostModel(0.01, 0.01, 0.01))
    t = r.trades.iloc[0]
    assert t["entry_price"] == pytest.approx(100.015)
    assert t["exit_price"] == pytest.approx(98.985)
    per_share_cost = 0.03 + 2 * 0.01  # spread + slippage on both sides is 0.03; plus commissions
    assert t["costs"] == pytest.approx(t["shares"] * per_share_cost)


def test_target_fill_pays_spread_but_no_slippage():
    rows = [FLAT, FLAT, FLAT, (101, 102.5, 100.9, 102)]
    t = run(rows, {1: Entry(stop=99, target=102)}, costs=CostModel(0.0, 0.01, 0.05)).trades.iloc[0]
    assert t["reason"] == "target" and t["exit_price"] == pytest.approx(102 - 0.005)


def test_entry_skipped_when_stop_is_not_below_fill():
    rows = [FLAT, FLAT, (98, 98.2, 97.8, 98), FLAT]  # gaps down under the 99 stop
    assert run(rows, {1: Entry(stop=99, target=110)}).trades.empty


def test_position_capped_by_max_position_pct():
    rows = [FLAT, FLAT, FLAT, FLAT]
    t = run(rows, {1: Entry(stop=99.99, target=110)}).trades.iloc[0]  # risk sizing alone: 10,000 shares
    assert t["shares"] == 1000  # capped at 100k equity / 100 price, no leverage


def test_open_position_is_closed_at_the_last_close():
    rows = [FLAT, FLAT, FLAT, (100, 100.5, 99.9, 100.4)]
    t = run(rows, {1: Entry(stop=95, target=110)}).trades.iloc[0]
    assert t["reason"] == "end_of_data" and t["exit_price"] == 100.4


def test_position_is_not_carried_overnight():
    d1 = make_bars([FLAT, FLAT, FLAT], day="2026-03-02")
    d2 = make_bars([(101, 101.5, 100.5, 101), FLAT], day="2026-03-03")
    bars = pd.concat([d1, d2])
    t = run_backtest(bars, Scripted({1: Entry(stop=95, target=110)}), costs=ZERO,
                     lookback_bars=None).trades.iloc[0]
    assert t["reason"] == "overnight" and t["exit_price"] == 101


def test_strategy_only_sees_closed_bars_up_to_the_current_one():
    rows = [FLAT] * 5
    s = Scripted({})
    run_backtest(make_bars(rows), s, costs=ZERO, lookback_bars=None)
    assert s.seen == [1, 2, 3, 4]  # never the final bar: nothing could fill after it


def test_lookback_window_limits_bars_shown():
    s = Scripted({})
    run_backtest(make_bars([FLAT] * 100), s, costs=ZERO, lookback_bars=80)
    assert max(s.seen) == 80
    with pytest.raises(ValueError):
        run_backtest(make_bars([FLAT] * 5), s, lookback_bars=10)
