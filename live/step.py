"""The pure core shared by the live runner and the parity test: no IB in here. Paper trading only.

It applies the same rules as backtests/engine.py around the strategy call:
- the strategy sees the last `lookback` closed bars, ending at the bar being decided;
- a position that is still open has `bars_held` raised by one before each call;
- an Entry only counts when flat, an Exit only when long; anything else is ignored.
"""

import pandas as pd

from strategies.base import Entry, Exit, Strategy

from .config import LOOKBACK_BARS
from .state import PositionState


def window(closed: pd.DataFrame, bar_start: pd.Timestamp, lookback: int = LOOKBACK_BARS) -> pd.DataFrame:
    """The bars the strategy is shown when deciding on the bar that starts at `bar_start` (its last row)."""
    end = closed.index.get_loc(bar_start) + 1
    return closed.iloc[max(0, end - lookback) : end]


def step(strategy: Strategy, bars: pd.DataFrame, position: PositionState | None) -> Entry | Exit | None:
    """Ask the strategy about the closed bar at the end of `bars`. Raises `position.bars_held` by one."""
    pos = None
    if position is not None:
        position.bars_held += 1
        pos = position.to_position()
    intent = strategy.on_bar(bars, pos)
    if isinstance(intent, Entry) and position is None:
        return intent
    if isinstance(intent, Exit) and position is not None:
        return intent
    return None
