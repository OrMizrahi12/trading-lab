"""Compare the state file with what IB says we hold and have resting. Paper trading only.

Reconcile never trades. It may cancel our own stale orders and drop a position that is
already gone at IB; anything else it cannot explain goes into `problems`, and the runner halts.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from .broker import Broker
from .config import ORDER_TIMEOUT_S
from .console import order_text, position_text
from .refs import owns
from .state import LiveState, PositionState

log = logging.getLogger("live")


@dataclass
class ReconcileResult:
    actions: list[str] = field(default_factory=list)  # what was changed, for the log
    problems: list[str] = field(default_factory=list)  # what cannot be explained: halt
    needs_protection: bool = False  # long, but the stop/target pair is missing or broken: place a new one
    state_text: str = ""  # what the state file said, for the console
    ib_text: str = ""  # what IB said: position and open orders on the symbol

    @property
    def ok(self) -> bool:
        return not self.problems


def ib_shares(ib, symbol: str) -> int:
    return int(sum(p.position for p in ib.positions() if p.contract.symbol == symbol))


def open_orders(ib, symbol: str):
    """Open trades for this symbol from every client (reqAllOpenOrders), not just ours."""
    ib.reqAllOpenOrders()
    return [t for t in ib.openTrades() if t.contract.symbol == symbol]


CANCEL_WAIT_S = 15


def cancel_confirmed(ib, broker: Broker, trade, res: ReconcileResult, why: str) -> bool:
    """Cancel one of our orders and wait until IB says it is gone. Not confirmed = a problem (never re-place on top)."""
    broker.cancel(trade.order)
    for _ in range(CANCEL_WAIT_S * 2):
        if trade.isDone():
            res.actions.append(f"cancelled order {trade.order.orderId} ({why})")
            return True
        ib.sleep(0.5)
    res.problems.append(f"order {trade.order.orderId} ({why}) is not confirmed cancelled after {CANCEL_WAIT_S}s")
    return False


def resolve_pending(ib, broker: Broker, state: LiveState, res: ReconcileResult) -> None:
    """The runner died after writing an entry's intent: find out what IB did with it and fill in the state.

    - the buy is still working: give it the usual timeout, then cancel what is left;
    - it filled and the bracket is resting: the position is adopted (shares, average price, order ids);
    - it filled and the stop/target already closed it: nothing to hold, noted;
    - it never filled: nothing to hold.
    The ordinary checks below then compare the adopted position with IB as for any other. The signal bar counts as
    stepped, so it is not acted on twice (the ref is also in `sent_refs`).
    """
    pend, symbol = state.pending_entry, state.symbol
    ref = pend["ref"]
    for t in [t for t in open_orders(ib, symbol) if t.order.orderRef == ref and t.order.action == "BUY"]:
        for _ in range(ORDER_TIMEOUT_S * 2):  # still working: it gets what time is left, as in the live path
            if t.isDone():
                break
            ib.sleep(0.5)
        if not t.isDone() and not cancel_confirmed(ib, broker, t, res, "entry still working after the restart"):
            return  # a problem was recorded; the pending record stays
    execs = [f.execution for f in ib.reqExecutions() if f.execution.orderRef == ref]
    bought = sum(int(e.shares) for e in execs if e.side == "BOT")
    cost = sum(int(e.shares) * float(e.price) for e in execs if e.side == "BOT")
    sold = sum(int(e.shares) for e in execs if e.side == "SLD")
    state.pending_entry = None
    state.last_bar_ts = max(filter(None, [state.last_bar_ts, pend["signal_bar"]]), key=pd.Timestamp)
    if bought == 0:
        res.actions.append(f"entry {ref} was sent but never filled; nothing to hold")
    elif ib_shares(ib, symbol) == 0:
        res.actions.append(f"entry {ref} bought {bought} @ {cost / bought:.2f} and is already closed at IB "
                           "(stop, target or exit); no trade record was written")
    else:
        kids = [t for t in open_orders(ib, symbol) if t.order.orderRef == ref and t.order.action == "SELL"]
        stop = next((t.order.orderId for t in kids if getattr(t.order, "orderType", "") == "STP"), None)
        target = next((t.order.orderId for t in kids if getattr(t.order, "orderType", "") == "LMT"), None)
        state.position = PositionState(shares=bought - sold, entry_time=pend["entry_time"], entry_price=cost / bought,
                                       stop=pend["stop"], target=pend["target"], stop_order_id=stop, target_order_id=target)
        res.actions.append(f"entry {ref} found at IB: adopted {bought - sold} {symbol} @ {cost / bought:.2f}")


def reconcile(ib, broker: Broker, state: LiveState) -> ReconcileResult:
    res = ReconcileResult()
    if state.pending_entry and state.position is None:
        resolve_pending(ib, broker, state, res)
        if res.problems:
            res.state_text = "an entry was in flight"
            res.ib_text = f"{ib_shares(ib, state.symbol)} {state.symbol}"
            return res
    shares = ib_shares(ib, state.symbol)
    orders = open_orders(ib, state.symbol)
    ours = [t for t in orders if owns(broker.tag, t.order.orderRef)]
    foreign = [t for t in orders if not owns(broker.tag, t.order.orderRef)]
    res.state_text = position_text(state.position)
    res.ib_text = (f"{shares} {state.symbol}; open orders: "
                   + ("none" if not orders else "; ".join(order_text(t, broker.tag) for t in orders)))

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
            cancel_confirmed(ib, broker, t, res, f"stale {t.order.action} while flat")
    elif shares == 0:
        # Stop or target filled (or someone closed it) while we were away: state and IB agree again.
        for t in ours:
            cancel_confirmed(ib, broker, t, res, "leftover after the position closed")
        res.actions.append(f"position of {pos.shares} {state.symbol} is gone at IB; state set to flat")
        state.position = None
    elif shares != pos.shares:
        res.problems.append(f"State says {pos.shares} {state.symbol}, IB holds {shares}")
    else:
        live_ids = {t.order.orderId for t in ours}
        wanted = {i for i in (pos.stop_order_id, pos.target_order_id) if i is not None}
        wanted_ok = wanted <= live_ids and pos.stop_order_id is not None
        extra = live_ids - wanted
        sized = all(int(t.order.totalQuantity) == pos.shares for t in ours)  # a partial fill leaves the children too big
        if wanted_ok and sized and not extra and len(wanted) == (1 if pos.target is None else 2):
            res.actions.append("position and its stop/target are in place")
        else:
            confirmed = [cancel_confirmed(ib, broker, t, res, "part of a broken stop/target pair") for t in ours]
            pos.stop_order_id = pos.target_order_id = None
            res.needs_protection = all(confirmed)  # only re-place once every old order is really gone

    for line in res.actions:
        log.info("reconcile: %s", line)
    for line in res.problems:
        log.error("reconcile PROBLEM: %s", line)
    return res
