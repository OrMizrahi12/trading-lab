"""Known-answer test: 12 hand-made SPY bars, two trades, every number worked out by hand.

Setup: $100,000 start, risk 1% per trade, default CostModel: commission $0.01/share
per side, spread $0.01 (half, $0.005, paid on every fill), slippage $0.01 (market-type
fills only, so not on the target limit sell). One session, 5-minute bars from 09:30 ET.

Bars (open, high, low, close). The strategy is scripted: it signals after bars 1 and 5.

    #   open    high    low     close
    0   100.00  100.20   99.80  100.00
    1   100.00  100.30   99.90  100.00   signal Entry(stop 99.00, target 102.00)
    2   100.00  100.40   99.60  100.20   ENTRY 1 at this open
    3   100.20  100.30   98.90   99.10   low 98.90 <= stop 99.00 -> STOP at 99.00
    4    99.10   99.30   98.95   99.20
    5    99.20   99.40   99.00   99.20   signal Entry(stop 98.10, target 100.70)
    6    99.20   99.50   99.10   99.40   ENTRY 2 at this open
    7    99.40  100.00   99.30   99.90
    8    99.90  100.50   99.80  100.40
    9   100.40  100.90  100.30  100.80   high 100.90 >= target 100.70 -> TARGET at 100.70
    10  100.80  100.90  100.60  100.70
    11  100.70  100.80  100.60  100.70

Trade 1 (stopped out)                         Trade 2 (hits target)
buy fill  100.00 + .005 + .01 = 100.015       99.20 + .005 + .01 = 99.215
risk/sh   100.015 - 99.00 + .02 + .005 + .01  99.215 - 98.10 + .035
          = 1.050                             = 1.150
shares    floor(1000.00 / 1.05) = 952         floor(990.004 / 1.15) = 860
entry $   952 * 100.015 = 95,214.28           860 * 99.215 = 85,324.90
entry fee 952 * .01 = 9.52                    860 * .01 = 8.60
cash      100,000 - 95,214.28 - 9.52          99,000.40 - 85,324.90 - 8.60
          = 4,776.20                          = 13,666.90
sell fill 99.00 - .005 - .01 = 98.985         100.70 - .005 = 100.695 (limit: no slippage)
exit $    952 * 98.985 = 94,233.72            860 * 100.695 = 86,597.70
exit fee  9.52                                8.60
cash      4,776.20 + 94,233.72 - 9.52         13,666.90 + 86,597.70 - 8.60
          = 99,000.40                         = 100,256.00
gross     952 * (99.00 - 100.00) = -952.00    860 * (100.70 - 99.20) = 1,290.00
net       952 * (98.985 - 100.015) - 19.04    860 * (100.695 - 99.215) - 17.20
          = -999.60                           = 1,255.60
costs     gross - net = 47.60                 34.40
          (fees 19.04 + spread 9.52           (fees 17.20 + spread 8.60
           + slippage 19.04)                   + slippage 8.60)
R         -999.60 / (952 * 1.05) = -1.0       1,255.60 / (860 * 1.15) = 1.26957

Final equity 100,256.00. Marked to each bar's close, equity is 100,166.60 after bar 2
(4,776.20 + 952 * 100.20), 99,000.40 after bar 3, and 100,010.90 after bar 8
(13,666.90 + 860 * 100.40).
"""

import pandas as pd
import pytest

from backtests.engine import run_backtest
from strategies.base import Entry, Strategy

ROWS = [
    (100.00, 100.20, 99.80, 100.00),
    (100.00, 100.30, 99.90, 100.00),
    (100.00, 100.40, 99.60, 100.20),
    (100.20, 100.30, 98.90, 99.10),
    (99.10, 99.30, 98.95, 99.20),
    (99.20, 99.40, 99.00, 99.20),
    (99.20, 99.50, 99.10, 99.40),
    (99.40, 100.00, 99.30, 99.90),
    (99.90, 100.50, 99.80, 100.40),
    (100.40, 100.90, 100.30, 100.80),
    (100.80, 100.90, 100.60, 100.70),
    (100.70, 100.80, 100.60, 100.70),
]


class Scripted(Strategy):
    def __init__(self, script):
        self.script = script

    def on_bar(self, bars, position):
        return self.script.get(len(bars) - 1)  # needs lookback_bars=None


@pytest.fixture(scope="module")
def result():
    idx = pd.date_range("2026-03-02 09:30", periods=len(ROWS), freq="5min", tz="America/New_York")
    bars = pd.DataFrame(ROWS, index=idx.tz_convert("UTC"),
                        columns=["open", "high", "low", "close"]).assign(volume=1000)
    script = {1: Entry(stop=99.00, target=102.00), 5: Entry(stop=98.10, target=100.70)}
    return run_backtest(bars, Scripted(script), lookback_bars=None), bars.index


def test_trade_stopped_out(result):
    res, idx = result
    t = res.trades.iloc[0]
    assert t["entry_time"] == idx[2] and t["exit_time"] == idx[3]
    assert t["reason"] == "stop"
    assert t["shares"] == 952
    assert t["entry_price"] == pytest.approx(100.015)
    assert t["exit_price"] == pytest.approx(98.985)
    assert t["gross_pnl"] == pytest.approx(-952.00)
    assert t["costs"] == pytest.approx(47.60)
    assert t["net_pnl"] == pytest.approx(-999.60)
    assert t["stop_risk"] == pytest.approx(999.60)  # 952 shares x 1.05 a share
    assert t["r_multiple"] == pytest.approx(-999.60 / 1000.00)  # 1R = 1% of the $100,000


def test_trade_hits_target(result):
    res, idx = result
    t = res.trades.iloc[1]
    assert t["entry_time"] == idx[6] and t["exit_time"] == idx[9]
    assert t["reason"] == "target"
    assert t["shares"] == 860
    assert t["entry_price"] == pytest.approx(99.215)
    assert t["exit_price"] == pytest.approx(100.695)
    assert t["gross_pnl"] == pytest.approx(1290.00)
    assert t["costs"] == pytest.approx(34.40)
    assert t["net_pnl"] == pytest.approx(1255.60)
    # 1R is 1% of the cash before the buy: 100,000 - 999.60 = 99,000.40 -> 990.004.
    assert t["stop_risk"] == pytest.approx(860 * 1.15)
    assert t["r_multiple"] == pytest.approx(1255.60 / 990.004)


def test_equity_curve_and_final_equity(result):
    res, idx = result
    assert len(res.trades) == 2
    assert res.equity.iloc[2] == pytest.approx(100_166.60)
    assert res.equity.iloc[3] == pytest.approx(99_000.40)
    assert res.equity.iloc[8] == pytest.approx(100_010.90)
    assert res.equity.iloc[-1] == pytest.approx(100_256.00)
    assert res.trades["net_pnl"].sum() == pytest.approx(256.00)
