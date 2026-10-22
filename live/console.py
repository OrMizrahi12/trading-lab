"""What the operator sees in the terminal. Console only: nothing here goes to events.log. Paper trading only."""

import logging

import pandas as pd

from .refs import owns
from .state import PositionState

NY = "America/New_York"
_console = logging.getLogger("live.console")
_console.propagate = False  # the log file keeps exactly what it had before


def say(text: str) -> None:
    _console.info(text)


def position_text(pos: PositionState | None) -> str:
    if pos is None:
        return "flat"
    target = "none" if pos.target is None else f"{pos.target:.2f}"
    return (f"long {pos.shares} @ {pos.entry_price:.2f} (stop {pos.stop:.2f}, target {target}, "
            f"{pos.bars_held} bars held)")


def minutes(delta: pd.Timedelta) -> str:
    return f"{int(delta.total_seconds() // 60)}m"


def bar_line(ts: pd.Timestamp, row: pd.Series, late: pd.Timedelta, position: str, outcome: str) -> str:
    when = ts.tz_convert(NY)
    return (f"bar {when:%Y-%m-%d %H:%M} ET  O {row['open']:.2f}  H {row['high']:.2f}  L {row['low']:.2f}  "
            f"C {row['close']:.2f}  (seen {minutes(late)} after close) | {position} | {outcome}")


def order_text(trade, tag: str) -> str:
    order = trade.order
    kind = getattr(order, "orderType", "")
    price = getattr(order, "lmtPrice", 0) or getattr(order, "auxPrice", 0)
    owner = "ours" if owns(tag, order.orderRef) else f"NOT ours ({order.orderRef or 'no ref'})"
    return f"#{order.orderId} {order.action} {kind} {order.totalQuantity}{f' @ {price}' if price else ''} [{owner}]"
