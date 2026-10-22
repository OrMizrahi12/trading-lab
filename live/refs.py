"""Idempotent order references: one entry signal, one ref, never sent twice. Paper trading only.

An entry's `orderRef` is `live:<strategy>:<symbol>:<session date>:<signal bar start>` (New York time), for example
`live:opening_range_breakout:QQQ:2026-10-22:0945`. All three orders of the bracket (the buy and its stop and target)
carry it, so IB itself remembers that the signal was acted on. Before sending, `evidence` looks for the ref in
IB's open orders, in today's executions and in this session's orders; anything found means: do not send.
"""

import pandas as pd

NY = "America/New_York"


def entry_ref(tag: str, signal_bar: pd.Timestamp) -> str:
    """`tag` is `live:<strategy>:<symbol>`; `signal_bar` is the start of the bar that gave the signal."""
    when = signal_bar.tz_convert(NY)
    return f"{tag}:{when:%Y-%m-%d}:{when:%H%M}"


def order_label(order) -> str:
    """IB's permId when it has one (it stays the same across sessions); orderId is 0 for orders of an earlier session."""
    perm = getattr(order, "permId", 0)
    return f"permId {perm}" if perm else f"order #{order.orderId}"


def owns(tag: str, ref: str) -> bool:
    """Is this orderRef ours? The plain tag (exits, repairs) or the tag plus a session and a bar (entries)."""
    return ref == tag or ref.startswith(tag + ":")


def evidence(ib, ref: str) -> list[str]:
    """Where IB already knows `ref`: open orders (any client), today's executions, orders of this session."""
    found = []
    ib.reqAllOpenOrders()
    for t in ib.openTrades():
        if t.order.orderRef == ref:
            found.append(f"open {order_label(t.order)} {t.order.action} {t.order.orderType} {t.order.totalQuantity}")
    for f in ib.reqExecutions():
        if f.execution.orderRef == ref:
            found.append(f"execution today: {f.execution.side} {f.execution.shares} @ {f.execution.price}")
    for t in ib.trades():
        if t.order.orderRef == ref and not t.isDone():
            continue  # already listed as an open order
        if t.order.orderRef == ref:
            found.append(f"{order_label(t.order)} {t.order.action} sent earlier in this session ({t.orderStatus.status})")
    return found
