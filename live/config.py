"""Settings for the live paper engine. Paper trading only."""

from pathlib import Path

from data_pipeline.config import HOST, PAPER_PORTS, PORT

CLIENT_ID = 6  # Unique per Gateway connection (1-5 are taken, see AGENTS.md).

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")
if HOST not in {"127.0.0.1", "localhost"}:
    raise RuntimeError(f"Refusing to connect: host {HOST} is not local")

STATE_DIR = Path(__file__).parent.parent / "data" / "live"  # gitignored runtime files

BAR_MINUTES = 5
POLL_SECONDS = 60
LOOKBACK_BARS = 200  # same window the backtester hands to on_bar (backtests/engine.py)
HISTORY_DURATION = "5 D"  # enough 5-minute bars to fill LOOKBACK_BARS across sessions

MAX_BAR_DELAY_MIN = 30  # a closed bar older than this when we see it gets a warning
MAX_SIGNAL_AGE_MIN = 30  # signals older than this are logged and skipped, never traded
FLATTEN_BEFORE_CLOSE_MIN = 10  # wall-clock flatten deadline; the delayed bars arrive too late for the strategies' own

ORDER_TIMEOUT_S = 30  # how long a marketable limit may rest before it is cancelled
LIMIT_OFFSET_PCT = 0.005  # marketable limit sits this fraction through the last known price
EXIT_MAX_REPRICES = 3  # a stuck exit is re-priced wider this many times, then the engine halts

HOLE_WAIT_MIN = 60  # a missing bar is waited for this long after it should have closed, then stepped past (entries stay blocked)
MAX_TICK_ERRORS = 5  # consecutive failed polls (feed errors, dropped connection) before the engine halts
