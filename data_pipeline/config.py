"""Settings for the data pipeline. Paper trading only."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 5  # Must be unique per connection to the Gateway (1-4 are taken).

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
SYMBOLS = ["AAPL", "SPY"]
WHAT_TO_SHOW = "TRADES"  # prices of actual trades
USE_RTH = True  # True = regular trading hours only (9:30-16:00 New York)
SESSION_TZ = "America/New_York"


@dataclass(frozen=True)
class BarSize:
    ib_setting: str  # IB's barSizeSetting string
    length: timedelta  # time covered by one bar
    chunk_duration: str  # IB durationStr for one request
    chunk_length: timedelta  # calendar time one request covers
    seed_days: int  # first run (no cache): how far back to go
    seed_in_trading_days: bool  # True: seed_days counts trading days, False: calendar days


BAR_SIZES = {
    "5min": BarSize("5 mins", timedelta(minutes=5), "1 W", timedelta(days=7), 30, False),
    "1min": BarSize("1 min", timedelta(minutes=1), "1 D", timedelta(days=1), 3, True),
}

MAX_TRADING_DAY_WALK = timedelta(days=10)  # cap for "N trading days" seeds (weekends, holidays)
MIN_REQUEST_GAP = 0.5  # seconds between historical requests (IB limits bursts per contract)
RETRY_BACKOFF = [15, 30, 60, 120]  # seconds to wait after each pacing error
