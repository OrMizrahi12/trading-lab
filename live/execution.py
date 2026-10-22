"""Orders: sizing, marketable limits with a timeout, and the stop/target pair. Paper trading only.

A marketable limit is a limit order priced through the market (a buy above the price, a sell
below it), so it fills like a market order but can never pay more than its cap. It rests for
`timeout_s` seconds at most and is then cancelled.

Every order goes through `Broker`, which checks the paper account first.
"""

import logging
import math
from dataclasses import dataclass

import pandas as pd
from ib_async import LimitOrder, StopOrder

from backtests.engine import CostModel
from backtests.sizing import RISK_PCT, FixedFractional
from strategies.base import Entry

from .broker import Broker
from .config import EXIT_MAX_REPRICES, LIMIT_OFFSET_PCT, ORDER_TIMEOUT_S
from .reconcile import ib_shares
from .state import PositionState

log = logging.getLogger("live")

POLL_S = 0.5  # how often we look at a resting order
CANCEL_WAIT_S = 15  # how long a cancel may take to be confirmed before we stop and call a human


class ExecutionHalt(RuntimeError):
    """An order could not be completed (a stuck exit). The runner stops trading and says so."""


@dataclass(frozen=True)
class Fill:
    shares: int
    price: float  # average fill price
    time: pd.Timestamp


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def net_liquidation(ib) -> float:
    """The first managed (paper) account's total value, cash plus positions, in dollars."""
    account = ib.managedAccounts()[0]
    for row in ib.accountSummary(account):
        if row.tag == "NetLiquidation" and row.currency in ("USD", "BASE") and row.account == account:
            return float(row.value)
    raise RuntimeError("IB did not report NetLiquidation")


class Executor:
    def __init__(self, ib, broker: Broker, contract, *, timeout_s: float = ORDER_TIMEOUT_S,
                 offset_pct: float = LIMIT_OFFSET_PCT, max_reprices: int = EXIT_MAX_REPRICES,
                 costs: CostModel = CostModel(), risk_pct: float = RISK_PCT, clock=utc_now):
        self.ib, self.broker, self.contract = ib, broker, contract
        self.timeout_s, self.offset_pct, self.max_reprices = timeout_s, offset_pct, max_reprices
        self.costs, self.sizer, self.clock = costs, FixedFractional(risk_pct), clock

    # -- sizing --------------------------------------------------------------------------

    def size(self, entry: Entry, ref_price: float) -> int:
        """Shares so that a stop-out costs at most 1% of the account, the backtester's formula, no leverage.

        `ref_price` is the last known price (the signal bar's close); the backtester sizes on the real next open.
        Returns 0 when the stop is not below the price (the backtester drops those entries too).
        """
        if not entry.stop < self.costs.buy(ref_price):
            return 0
        # Size on the worst price the limit allows, so a fill at the cap still risks no more than 1%.
        fill = self.costs.buy(ref_price * (1 + self.offset_pct))
        risk_ps = (fill - entry.stop + 2 * self.costs.commission_per_share
                   + self.costs.spread / 2 + self.costs.slippage_per_share)
        if risk_ps <= 0:
            return 0
        equity = net_liquidation(self.ib)
        qty = self.sizer.shares(equity=equity, fill=fill, risk_per_share=risk_ps,
                                entry_time=self.clock(), trades=[])
        return max(0, min(qty, math.floor(equity / fill)))

    # -- marketable limits ---------------------------------------------------------------

    def _limit(self, action: str, ref: float, widen: int = 0) -> float:
        offset = self.offset_pct * (2**widen)
        return round(ref * (1 + offset) if action == "BUY" else ref * (1 - offset), 2)

    def _wait(self, trade, seconds: float) -> None:
        for _ in range(max(1, math.ceil(seconds / POLL_S))):
            if trade.isDone():
                return
            self.ib.sleep(POLL_S)

    def _send(self, action: str, shares: int, ref: float, widen: int = 0) -> Fill | None:
        """One marketable limit. Waits up to the timeout, cancels what is left, returns what filled (or None)."""
        order = LimitOrder(action, shares, self._limit(action, ref, widen), tif="DAY")
        trade = self.broker.place(self.contract, order)
        if trade is None:  # dry run
            return None
        self._wait(trade, self.timeout_s)
        if not trade.isDone():
            log.warning("exec: %s %d %s did not finish in %ss, cancelling", action, shares, self.contract.symbol, self.timeout_s)
            self.broker.cancel(order)
            self._wait(trade, CANCEL_WAIT_S)
            if not trade.isDone():  # it may still fill: we cannot know what we hold, so stop here
                raise ExecutionHalt(f"{action} order {order.orderId} is not confirmed cancelled after "
                                    f"{CANCEL_WAIT_S}s; check IB before trading")
        filled = int(trade.orderStatus.filled)
        if filled == 0:
            return None
        return Fill(filled, float(trade.orderStatus.avgFillPrice), self.clock())

    def buy(self, shares: int, ref: float) -> Fill | None:
        """Entry. An order that does not fill in time is cancelled and dropped, as the backtester drops it."""
        fill = self._send("BUY", shares, ref)
        if fill is None:
            log.warning("exec: entry of %d %s not filled, dropped", shares, self.contract.symbol)
        elif fill.shares < shares:
            log.warning("exec: entry partly filled, %d of %d; keeping %d", fill.shares, shares, fill.shares)
        return fill

    # -- protection ----------------------------------------------------------------------

    def protect(self, pos: PositionState) -> None:
        """Place the stop (and the target, if any) as a one-cancels-other pair resting at IB. Records the order ids."""
        symbol = self.contract.symbol
        stop = StopOrder("SELL", pos.shares, round(pos.stop, 2), tif="GTC")  # GTC: stays if the runner is down at the close
        orders = [stop]
        if pos.target is not None:
            orders.append(LimitOrder("SELL", pos.shares, round(pos.target, 2), tif="GTC"))
            group = f"{self.broker.tag}:{self.clock().strftime('%H%M%S')}"
            for o in orders:  # when one fills, IB cancels the other
                o.ocaGroup, o.ocaType = group, 1
        trades = [self.broker.place(self.contract, o) for o in orders]
        if trades[0] is None:  # dry run
            return
        pos.stop_order_id = trades[0].order.orderId
        pos.target_order_id = trades[1].order.orderId if len(trades) > 1 else None
        self.ib.sleep(1)  # give IB a moment to accept or reject
        dead = [t for t in trades if t.isDone() and t.orderStatus.status != "Filled"]
        if dead:
            for t in trades:
                if not t.isDone():
                    self.broker.cancel(t.order)
            raise ExecutionHalt(f"IB did not accept the stop/target for {pos.shares} {symbol}: "
                                f"{[(t.order.orderId, t.orderStatus.status) for t in dead]}")
        log.info("exec: protecting %d %s, stop %.2f target %s", pos.shares, symbol, pos.stop, pos.target)

    def cancel_protection(self, pos: PositionState) -> int:
        """Cancel the stop/target and wait for IB to confirm. Returns the shares they filled (a stop may beat us).

        The order ids stay on `pos` so the runner can still look up what filled; `protect` overwrites them.
        """
        ids = {pos.stop_order_id, pos.target_order_id} - {None}
        ours = [t for t in self.ib.trades() if t.order.orderRef == self.broker.tag and t.order.orderId in ids]
        for t in ours:
            if not t.isDone():
                self.broker.cancel(t.order)
                self._wait(t, CANCEL_WAIT_S)
                if not t.isDone():
                    raise ExecutionHalt(f"order {t.order.orderId} is not confirmed cancelled after "
                                        f"{CANCEL_WAIT_S}s; check IB before trading")
        return sum(int(t.orderStatus.filled) for t in ours)

    # -- exit ----------------------------------------------------------------------------

    def exit(self, pos: PositionState, ref: float) -> Fill | None:
        """Sell `pos.shares`. Stop and target are cancelled first; the sell is re-priced wider if it stalls.

        Never sells more than IB holds minus what the stop/target already sold. Returns None if nothing was left
        to sell (the stop or target got there first). If the position cannot be closed, the stop/target are put
        back and ExecutionHalt is raised: a stuck exit is never silent. An order whose cancel is not confirmed
        also raises, so two sells can never be live at once.
        """
        symbol = self.contract.symbol
        if self.broker.dry_run:
            log.info("DRY RUN: would sell %d %s", pos.shares, symbol)
            return None
        already_sold = self.cancel_protection(pos)
        left = min(ib_shares(self.ib, symbol), pos.shares - already_sold)
        sold, proceeds = 0, 0.0
        for attempt in range(self.max_reprices + 1):
            if left <= 0:
                break
            fill = self._send("SELL", left, ref, widen=attempt)
            if fill is not None:
                sold, proceeds, left = sold + fill.shares, proceeds + fill.shares * fill.price, left - fill.shares
            if left > 0:
                log.warning("exec: %d %s still unsold after attempt %d", left, symbol, attempt + 1)
        if left > 0:
            pos.shares = left
            self.protect(pos)
            raise ExecutionHalt(f"could not sell {left} {symbol} after {self.max_reprices + 1} attempts")
        return Fill(sold, proceeds / sold, self.clock()) if sold else None
