"""The one Strategy interface shared by the backtester and the live paper trader.

A strategy is a pure decision function: it is shown the bars that have closed so
far (and its open position, if any) and answers with an intent. It never sends
orders, sizes by account, or touches Interactive Brokers (IB). The engine around
it (the backtester now, the paper trader later) turns the intent into fills, so a
strategy is written once and behaves the same in both. Paper trading / education
only. Nothing here is financial advice.

Timing rule: a decision is made at a bar's close and filled at the NEXT bar's
open, because the close is only known once the bar is over.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Position:
    """An open long position (a bet that the price rises)."""

    entry_time: pd.Timestamp
    entry_price: float
    stop: float  # sell here to cap the loss
    target: float  # sell here to take the profit
    bars_held: int = 0


@dataclass(frozen=True)
class Entry:
    """Buy at the next bar's open, with a bracket (a stop and a profit target attached).

    The share count is not here: the engine sizes it from the risk per share,
    entry - stop (called R, the dollars risked per share). `stop` and `target`
    are computed from the signal bar's close, so the engine must re-check
    stop < actual fill price, since the real fill is the next open.
    """

    stop: float
    target: float
    reason: str = "entry"


@dataclass(frozen=True)
class Exit:
    """Sell the open position at the next bar's open."""

    reason: str


class Strategy(ABC):
    @abstractmethod
    def on_bar(self, bars: pd.DataFrame, position: Position | None) -> Entry | Exit | None:
        """Decide what to do now that the last row of `bars` has closed.

        `bars` holds every bar up to and including the bar that just closed (UTC
        index, columns open/high/low/close/volume), so the last row is the signal
        bar. Use only these rows (no look-ahead). Return an Entry, an Exit, or
        None for "do nothing". Must be deterministic given the same arguments.
        """


def check_bracket(
    position: Position, bar: pd.Series, priority: str = "stop_first"
) -> tuple[str, float] | None:
    """Did this bar hit the position's stop or target? Returns (reason, fill price).

    Both engines use this so the rule lives in one place. A bar only tells us its
    high and low, not the order they happened in. If it touches both stop and
    target we cannot know which came first, so `stop_first` assumes the worse
    case (the stop). A gap (the bar opens past a level) fills at the open, not at
    the level, which is the worse price for a stop and the better one for a target.
    The live trader will instead hand the bracket to IB, which enforces it.
    """
    if priority != "stop_first":
        raise ValueError(f"unsupported priority: {priority}")
    if bar["low"] <= position.stop:
        return "stop", min(position.stop, bar["open"])
    if bar["high"] >= position.target:
        return "target", max(position.target, bar["open"])
    return None
