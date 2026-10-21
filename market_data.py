"""Qualify contracts, request snapshot quotes and bars from IB Gateway (paper). Paper trading only."""

import sys
import time
from pathlib import Path

import pandas as pd
from ib_async import IB, Forex, Stock, util

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 3  # Must be unique per connection to the Gateway.

LIVE, DELAYED = 1, 3  # IB market data types
SNAPSHOT_WAIT = 5  # seconds to wait for IB to answer each snapshot
REALTIME_SECONDS = 65  # how long to listen for 5-second bars (about one minute)
DATA_DIR = Path(__file__).parent / "data"

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")


# IB pacing limits (pacing = IB rejects or disconnects clients that send requests too fast):
HIST_MAX_PER_WINDOW = 60  # historical requests allowed per 10 minutes
HIST_WINDOW = 600  # seconds
HIST_SAME_REQUEST_GAP = 15  # seconds before an identical historical request may repeat
MKT_DATA_LINES = 100  # default simultaneous market data lines (subscriptions)


class Throttle:
    """Sleeps just long enough to keep historical requests under IB's pacing limits.

    Call wait(ib, key) right before each reqHistoricalData. `key` identifies the request
    (contract, bar size, what-to-show, ...); repeating the same key is held for 15 seconds.
    """

    def __init__(self, max_calls=HIST_MAX_PER_WINDOW - 5, window=HIST_WINDOW, same_gap=HIST_SAME_REQUEST_GAP):
        self.max_calls, self.window, self.same_gap = max_calls, window, same_gap  # 5-request safety margin
        self.times = []  # send times of recent requests (monotonic seconds)
        self.last_by_key = {}

    def wait(self, ib, key=None):
        now = time.monotonic()
        self.times = [t for t in self.times if now - t < self.window]
        delay = 0.0
        if len(self.times) >= self.max_calls:
            delay = max(delay, self.window - (now - self.times[0]))
        if key in self.last_by_key:
            delay = max(delay, self.same_gap - (now - self.last_by_key[key]))
        if delay > 0:
            print(f"    throttle: waiting {delay:.1f}s to respect IB pacing")
            ib.sleep(delay)
        now = time.monotonic()
        self.times.append(now)
        self.last_by_key[key] = now


def check_line_room(ib, extra=1, limit=MKT_DATA_LINES):
    """Raise before opening `extra` more market data lines if we would reach the limit."""
    open_lines = len(ib.tickers())
    if open_lines + extra > limit:
        raise RuntimeError(f"{open_lines} market data lines open, +{extra} would exceed {limit}; cancel some first")


hist_throttle = Throttle()


def show_errors(reqId, errorCode, errorString, contract):
    """Print every message IB sends back, verbatim."""
    print(f"    IB message: reqId={reqId} code={errorCode} {errorString!r}")


def qualify(ib):
    contracts = [
        Stock("AAPL", "SMART", "USD"),
        Stock("SPY", "SMART", "USD"),
        Forex("EURUSD"),  # IDEALPRO is the forex venue
    ]
    ib.qualifyContracts(*contracts)
    for c in contracts:
        print(f"{c.symbol}/{c.currency} {c.secType}: conId={c.conId} primaryExchange={c.primaryExchange!r} exchange={c.exchange}")
    return contracts


def snapshot(ib, contracts, market_data_type, label):
    print(f"\n--- {label} (market data type {market_data_type}) ---")
    ib.reqMarketDataType(market_data_type)
    for c in contracts:
        print(f"{c.symbol}{c.currency if c.secType == 'CASH' else ''}:")
        check_line_room(ib)
        ticker = ib.reqMktData(c, genericTickList="", snapshot=True)
        ib.sleep(SNAPSHOT_WAIT)
        print(
            f"    bid={ticker.bid} ask={ticker.ask} last={ticker.last} close={ticker.close} "
            f"marketDataType={ticker.marketDataType}"
        )
        ib.cancelMktData(c)


def save_and_show(df, name):
    """Write bars to data/<name>.csv and print the first and last rows."""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.csv"
    df.to_csv(path)
    print(f"    saved {len(df)} rows -> {path.relative_to(path.parent.parent)}")
    print(df.head(3).to_string())
    if len(df) > 3:
        print("    ...")
        print(df.tail(2).to_string())


def historical_bars(ib, contract, what_to_show, use_rth, label):
    """Last 1 day of 5-minute bars. Returns a DataFrame, or None if IB sent nothing."""
    print(f"\n--- historical 5-min {what_to_show} bars: {label} ---")
    hist_throttle.wait(ib, (contract.conId, what_to_show, use_rth))
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",  # empty = now
        durationStr="1 D",
        barSizeSetting="5 mins",
        whatToShow=what_to_show,
        useRTH=use_rth,  # True = regular trading hours only
        formatDate=2,  # timestamps as UTC
    )
    if not bars:
        print("    no bars returned")
        return None
    df = util.df(bars).set_index("date")
    save_and_show(df, f"{contract.symbol}{contract.currency if contract.secType == 'CASH' else ''}_5min_{what_to_show}")
    return df


def resample_bars(df, rule):
    """Combine small bars into bigger ones. rule is a pandas offset such as "1min" or "15min".

    A bigger bar is built from the smaller ones in its time window:
    open = first open, high = max high, low = min low, close = last close, volume = sum.
    """
    return df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])


def resample_aapl_demo(rule="15min"):
    """Offline: resample the saved AAPL 5-minute bars (no IB connection needed)."""
    src = DATA_DIR / "AAPL_5min_TRADES.csv"
    five = pd.read_csv(src, index_col="date", parse_dates=True)[["open", "high", "low", "close", "volume"]]
    bigger = resample_bars(five, rule)
    print(f"\n--- AAPL {len(five)} five-minute bars -> {len(bigger)} {rule} bars ---")
    print("\n5-minute bars (first 6):")
    print(five.head(6).to_string())
    print(f"\n{rule} bars (first 2, each built from 3 rows above):")
    print(bigger.head(2).to_string())
    save_and_show(bigger, f"AAPL_{rule}_resampled")
    return five, bigger


def realtime_bars(ib, contract, seconds, label):
    """Collect 5-second real-time bars for `seconds`, then resample to 1-minute bars."""
    print(f"\n--- real-time 5-sec TRADES bars: {label}, listening {seconds}s ---")
    check_line_room(ib)
    bars = ib.reqRealTimeBars(contract, 5, "TRADES", useRTH=False)
    ib.sleep(seconds)
    ib.cancelRealTimeBars(bars)
    if not bars:
        print("    no bars arrived")
        return None
    df = pd.DataFrame(
        [{"time": b.time, "open": b.open_, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    ).set_index("time")
    save_and_show(df, f"{contract.symbol}_5sec_TRADES")

    # For 5-minute bars, change "1min" to "5min"; nothing else changes.
    one_min = resample_bars(df, "1min")
    print(f"\n    resampled {len(df)} five-second bars -> {len(one_min)} one-minute bars")
    save_and_show(one_min, f"{contract.symbol}_1min_resampled")
    return one_min


def main():
    if "--resample-aapl" in sys.argv:
        resample_aapl_demo()
        return
    ib = IB()
    ib.errorEvent += show_errors
    # readonly=True: the session can read data but cannot place orders.
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)
    try:
        accounts = ib.managedAccounts()
        if not all(a.startswith("DU") for a in accounts):
            raise RuntimeError(f"Non-paper account(s): {accounts}")

        aapl, spy, eurusd = qualify(ib)
        if "--snapshots" in sys.argv:
            snapshot(ib, [aapl, spy, eurusd], LIVE, "live")
            snapshot(ib, [aapl, spy, eurusd], DELAYED, "delayed")

        ib.reqMarketDataType(DELAYED)  # fall back to delayed data where live is not subscribed
        historical_bars(ib, aapl, "TRADES", True, "AAPL")
        historical_bars(ib, eurusd, "MIDPOINT", False, "EURUSD")  # forex has no TRADES; it trades 24h
        realtime_bars(ib, spy, REALTIME_SECONDS, "SPY")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
