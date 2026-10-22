"""A FakeIB that replays bars and fills orders by the backtester's own rules, for the parity test.

- A marketable limit fills at the NEXT bar's open, cost-adjusted (`CostModel.buy` / `sell`).
- A resting stop or target is checked inside every bar with `check_bracket` (stop first; gaps fill at the open),
  including the bar the entry filled at the open of.
- A position still open when a new session starts is sold at that open (the engine's "overnight" rule).
"""

import indicators
from backtests.engine import CostModel
from strategies.base import Position, check_bracket
from tests.fake_ib import FakeIB


class SimIB(FakeIB):
    def __init__(self, bars, costs: CostModel = CostModel()):
        super().__init__()
        self.bars, self.costs = bars, costs
        self.session = list(bars.index.tz_convert(indicators.SESSION_TZ).date)
        self.i = -1  # index of the newest bar that has traded
        self.connect("127.0.0.1", 4002)

    def _try_fill(self, trade):
        order = trade.order
        if order.orderType != "LMT" or getattr(order, "ocaGroup", ""):
            return  # stops and targets rest until advance() finds them hit
        nxt = self.i + 1
        open_ = float(self.bars["open"].iloc[nxt])
        self.now = self.bars.index[nxt]
        price = self.costs.buy(open_) if order.action == "BUY" else self.costs.sell(open_)
        self._fill(trade, int(order.totalQuantity), price)

    def advance(self, i: int) -> None:
        """Bar `i` has traded: apply the overnight rule, then check the resting stop and target against it."""
        self.i, self.now = i, self.bars.index[i]
        bar = self.bars.iloc[i]
        resting = [t for t in self.openTrades() if t.contract.symbol == "QQQ" or True]
        stop = next((t for t in resting if t.order.orderType == "STP"), None)
        target = next((t for t in resting if t.order.orderType == "LMT" and getattr(t.order, "ocaGroup", "")), None)
        if stop is None:
            return
        if i > 0 and self.session[i] != self.session[i - 1]:
            self._fill(stop, stop.orderStatus.remaining, self.costs.sell(float(bar["open"])))
            return
        pos = Position(self.now, 0.0, float(stop.order.auxPrice),
                       float(target.order.lmtPrice) if target else float("inf"))
        hit = check_bracket(pos, bar)
        if hit is None:
            return
        kind, raw = hit
        if kind == "stop":
            self._fill(stop, stop.orderStatus.remaining, self.costs.sell(raw))
        else:
            self._fill(target, target.orderStatus.remaining, self.costs.sell_limit(raw))
