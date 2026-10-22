"""Compare the state file with what IB says we hold and have resting. Paper trading only.

Reconcile never trades. It may cancel our own stale orders and drop a position that is
already gone at IB; anything else it cannot explain goes into `problems`, and the runner halts.
"""

import logging
from dataclasses import dataclass, field

from .broker import Broker
from .state import LiveState

log = logging.getLogger("live")


@dataclass
class ReconcileResult:
    actions: list[str] = field(default_factory=list)  # what was changed, for the log
    problems: list[str] = field(default_factory=list)  # what cannot be explained: halt
    needs_protection: bool = False  # long, but the stop/target pair is missing or broken: place a new one

    @property
    def ok(self) -> bool:
        return not self.problems


def ib_shares(ib, symbol: str) -> int:
    return int(sum(p.position for p in ib.positions() if p.contract.symbol == symbol))


def open_orders(ib, symbol: str):
    """Open trades for this symbol from every client (reqAllOpenOrders), not just ours."""
    ib.reqAllOpenOrders()
    return [t for t in ib.openTrades() if t.contract.symbol == symbol]


def reconcile(ib, broker: Broker, state: LiveState) -> ReconcileResult:
    res = ReconcileResult()
    shares = ib_shares(ib, state.symbol)
    orders = open_orders(ib, state.symbol)
    ours = [t for t in orders if t.order.orderRef == broker.tag]
    foreign = [t for t in orders if t.order.orderRef != broker.tag]

    if foreign:
        res.problems.append(
            f"{len(foreign)} open order(s) on {state.symbol} are not ours: "
            f"{[(t.order.orderId, t.order.orderRef, t.order.action) for t in foreign]}"
        )

    pos = state.position
    if pos is None:
        if shares != 0:
            res.problems.append(f"State is flat but IB holds {shares} {state.symbol}")
        for t in ours:
            broker.cancel(t.order)
            res.actions.append(f"cancelled stale order {t.order.orderId} ({t.order.action}) while flat")
    elif shares == 0:
        # Stop or target filled (or someone closed it) while we were away: state and IB agree again.
        for t in ours:
            broker.cancel(t.order)
            res.actions.append(f"cancelled leftover order {t.order.orderId}")
        res.actions.append(f"position of {pos.shares} {state.symbol} is gone at IB; state set to flat")
        state.position = None
    elif shares != pos.shares:
        res.problems.append(f"State says {pos.shares} {state.symbol}, IB holds {shares}")
    else:
        live_ids = {t.order.orderId for t in ours}
        wanted = {i for i in (pos.stop_order_id, pos.target_order_id) if i is not None}
        wanted_ok = wanted <= live_ids and pos.stop_order_id is not None
        extra = live_ids - wanted
        if wanted_ok and not extra and len(wanted) == (1 if pos.target is None else 2):
            res.actions.append("position and its stop/target are in place")
        else:
            for t in ours:  # start from a clean slate: cancel the broken remains
                broker.cancel(t.order)
                res.actions.append(f"cancelled order {t.order.orderId} of a broken stop/target pair")
            pos.stop_order_id = pos.target_order_id = None
            res.needs_protection = True

    for line in res.actions:
        log.info("reconcile: %s", line)
    for line in res.problems:
        log.error("reconcile PROBLEM: %s", line)
    return res
