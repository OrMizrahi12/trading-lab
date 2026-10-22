"""Helpers shared by the live engine tests."""

import math
from pathlib import Path

import pandas as pd
import pytest

from live.state import PositionState
from strategies.base import Entry, Exit, Strategy

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"


def cached_bars(symbol: str = "QQQ", start: str = "2025-09-02", end: str = "2025-12-05") -> pd.DataFrame:
    """A slice of the research cache (never the holdout). Skips the test when the cache is not on this machine."""
    path = CACHE / f"{symbol}_5min.parquet"
    if not path.exists():
        pytest.skip(f"{path} not available")
    bars = pd.read_parquet(path)
    return bars.loc[pd.Timestamp(start, tz="UTC") : pd.Timestamp(end, tz="UTC")]


class Recorder(Strategy):
    """Wraps a strategy and records every on_bar call: (signal bar start, position, intent).

    The backtester and the live path both drive one of these, so their signals can be compared.
    """

    def __init__(self, inner: Strategy):
        self.inner = inner
        self.calls: list[tuple] = []

    def reset(self) -> None:
        if hasattr(self.inner, "reset"):
            self.inner.reset()

    def on_bar(self, bars, position):
        intent = self.inner.on_bar(bars, position)
        self.calls.append((bars.index[-1], position, intent))
        return intent

    def signals(self) -> list[tuple]:
        """What the strategy decided, as comparable tuples: (bar start, kind, stop, target, reason)."""
        out = []
        for ts, _, intent in self.calls:
            if isinstance(intent, Entry):
                out.append((ts, "entry", intent.stop, intent.target, intent.reason))
            elif isinstance(intent, Exit):
                out.append((ts, "exit", None, None, intent.reason))
        return out


def position_state(position, shares: int = 1) -> PositionState | None:
    """A base.Position (as the engine passed it) as the live path's state record."""
    if position is None:
        return None
    return PositionState(
        shares=shares,
        entry_time=position.entry_time.isoformat(),
        entry_price=position.entry_price,
        stop=position.stop,
        target=None if math.isinf(position.target) else position.target,
        bars_held=position.bars_held - 1,  # step() raises it by one before the call, like the engine
    )
