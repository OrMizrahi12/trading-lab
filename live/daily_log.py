"""One log file per engine per day, for several engines in one process. Paper trading only.

All the live modules log to the one logger name `live` (and the operator's lines to `live.console`). With several
engines in a single thread, the engine that is running is recorded in a context variable (`running(label)`), and each
engine's `DailyFileHandler` keeps only the records written while its own label was set:

    data/live/<strategy>_<symbol>/logs/2026-10-23.log      one file per day, New York date
    data/live/engine/logs/2026-10-23.log                   records written outside any engine (startup, shutdown)

The console shows the same lines with a `[SYMBOL strategy]` prefix so the interleaved output stays readable.
"""

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import STATE_DIR
from .state import run_dir

NY = ZoneInfo("America/New_York")
ENGINE_DIR = "engine"
FORMAT = "%(asctime)s %(levelname)s %(name)s | %(message)s"
current: ContextVar[str | None] = ContextVar("live_engine", default=None)


@contextmanager
def running(label: str | None):
    """Everything logged inside the block belongs to the engine `label`."""
    token = current.set(label)
    try:
        yield
    finally:
        current.reset(token)


def new_york_today() -> str:
    return datetime.now(NY).strftime("%Y-%m-%d")


class DailyFileHandler(logging.Handler):
    """Appends the records of one engine (`label`; None = outside any engine) to `<directory>/<date>.log`."""

    def __init__(self, directory: Path, label: str | None, today=new_york_today):
        super().__init__(logging.INFO)
        self.directory, self.label, self.today = Path(directory), label, today
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        if current.get() != self.label:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{self.today()}.log").open("a") as f:
                f.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


class _Prefix(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        label = current.get()
        record.engine = f"[{label}] " if label else ""
        return True


def setup_multi_logging(labels_and_dirs: dict[str, Path], root: Path = STATE_DIR, today=new_york_today) -> list:
    """Attach the console and the per-engine daily files. Returns the handlers (tests remove them again)."""
    live, console = logging.getLogger("live"), logging.getLogger("live.console")
    live.setLevel(logging.INFO)
    console.setLevel(logging.INFO)
    out = []
    for logger, fmt in ((live, "%(asctime)s %(levelname)s %(engine)s%(message)s"),
                        (console, "%(asctime)s | %(engine)s%(message)s")):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S" if logger is console else None))
        handler.addFilter(_Prefix())
        logger.addHandler(handler)
        out.append((logger, handler))
    files = [DailyFileHandler(Path(root) / ENGINE_DIR / "logs", None, today)]
    files += [DailyFileHandler(directory / "logs", label, today) for label, directory in labels_and_dirs.items()]
    for logger in (live, console):  # the console logger does not propagate, so it needs the files too
        for handler in files:
            logger.addHandler(handler)
            out.append((logger, handler))
    return out


def engine_dirs(pairs, root: Path = STATE_DIR) -> dict[str, Path]:
    return {f"{p.symbol} {p.strategy}": run_dir(p.strategy, p.symbol, root) for p in pairs}
