"""The engine's state file: what it believes it holds, written atomically after every change. Paper trading only."""

import hashlib
import json
import math
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from strategies.base import Position

from .config import STATE_DIR

VERSION = 1


class StateError(RuntimeError):
    """The state file cannot be used (wrong settings, locked by another runner, unknown version)."""


class StopFileError(RuntimeError):
    """The STOP file exists (the kill switch was used): the runner does not start or keep running."""


def settings_key(strategy: str, symbol: str, settings: dict) -> str:
    """Short stable id of one (strategy, symbol, settings) combination."""
    text = f"{strategy}|{symbol}|{sorted((k, repr(v)) for k, v in settings.items())}"
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def run_dir(strategy: str, symbol: str, root: Path = STATE_DIR) -> Path:
    return root / f"{strategy}_{symbol}"


@dataclass
class PositionState:
    shares: int
    entry_time: str  # ISO UTC, the nominal fill bar (signal bar + 5 min), as in the backtester
    entry_price: float  # the actual average fill
    stop: float
    target: float | None  # None = no target
    bars_held: int = 0
    stop_order_id: int | None = None
    target_order_id: int | None = None
    # Set when IB closed the position (stop, target) before the delayed bars show it. The runner drops the
    # position when it steps the first bar that contains this time, which is what the backtester does.
    closed_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    def to_position(self) -> Position:
        return Position(
            entry_time=pd.Timestamp(self.entry_time),
            entry_price=self.entry_price,
            stop=self.stop,
            target=math.inf if self.target is None else self.target,
            bars_held=self.bars_held,
        )


@dataclass
class LiveState:
    strategy: str
    symbol: str
    key: str  # settings_key: a state file only fits the settings that made it
    last_bar_ts: str | None = None  # start of the last closed bar already stepped (ISO UTC)
    position: PositionState | None = None
    halted: str | None = None  # why the engine stopped trading, if it did
    # Written BEFORE an entry bracket is sent, cleared when its outcome is in `position`: if the runner dies in
    # between, the next start's reconcile finds the entry at IB through this record (ref, shares, stop, target).
    pending_entry: dict | None = None
    sent_refs: list[str] = field(default_factory=list)  # entry refs sent in the last sessions: never sent again
    version: int = VERSION
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "LiveState":
        raw = json.loads(text)
        if raw.get("version") != VERSION:
            raise StateError(f"Unknown state version {raw.get('version')}")
        pos = raw.pop("position")
        return cls(**raw, position=PositionState(**pos) if pos else None)


def load(path: Path, strategy: str, symbol: str, key: str) -> LiveState:
    """Read the state file, or start a fresh flat state if there is none. Refuse a file made with other settings."""
    if not path.exists():
        return LiveState(strategy=strategy, symbol=symbol, key=key)
    state = LiveState.from_json(path.read_text())
    if (state.strategy, state.symbol, state.key) != (strategy, symbol, key):
        raise StateError(
            f"{path} belongs to {state.strategy}/{state.symbol}/{state.key}, not {strategy}/{symbol}/{key}; "
            "move it away if the settings really changed"
        )
    return state


def save(path: Path, state: LiveState) -> None:
    """Atomic write: a crash leaves either the old file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        f.write(state.to_json())
        f.flush()
        os.fsync(f.fileno())  # on disk before the rename, so a power cut cannot leave an empty state file
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claim(path: Path) -> None:
    """Create the pid file, failing if it already exists (atomic: two runners cannot both succeed)."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))


@contextmanager
def lock(directory: Path):
    """One runner per state directory. A lock left by a dead process is taken over."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "runner.pid"
    for attempt in range(2):
        try:
            _claim(path)
            break
        except FileExistsError:
            try:
                other = int(path.read_text())
            except (ValueError, FileNotFoundError):
                other = 0
            if other and other != os.getpid() and _pid_alive(other):
                raise StateError(f"Another runner (pid {other}) holds {path}") from None
            path.unlink(missing_ok=True)  # stale: its owner is gone
            if attempt == 1:
                raise StateError(f"Could not take over {path}") from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
