"""Incremental download of IB historical bars into the Parquet cache. Paper trading only."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from ib_async import IB, Stock, util

from market_data import Throttle, hist_throttle

from . import cache
from .config import (
    BAR_SIZES,
    CACHE_DIR,
    CLIENT_ID,
    HOST,
    MAX_TRADING_DAY_WALK,
    MIN_REQUEST_GAP,
    PORT,
    RETRY_BACKOFF,
    SESSION_TZ,
    USE_RTH,
    WHAT_TO_SHOW,
)

DELAYED = 3  # IB market data type: fall back to delayed data where live is not subscribed


class PacingError(RuntimeError):
    """IB kept rejecting requests for going too fast, even after backing off."""


def connect_paper() -> IB:
    """Connect read-only to the paper Gateway and refuse anything but a DU (paper) account."""
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)
    accounts = ib.managedAccounts()
    if not accounts or not all(a.startswith("DU") for a in accounts):
        ib.disconnect()
        raise RuntimeError(f"Non-paper account(s): {accounts}")
    ib.reqMarketDataType(DELAYED)
    return ib


def _classify(errors):
    """Return ("pacing"|"fatal"|None, message) for the IB messages seen during one request."""
    for code, text in errors:
        low = text.lower()
        if code == 100 or "pacing violation" in low:
            return "pacing", text
    for code, text in errors:
        low = text.lower()
        informational = code == 165 or 2100 <= code < 2200  # farm status messages
        if not informational and "no data" not in low:  # "HMDS query returned no data" = empty range
            return "fatal", f"IB error {code}: {text}"
    return None, ""


def fetch_chunk(ib, contract, end, bar_size, throttle: Throttle = hist_throttle) -> pd.DataFrame:
    """One historical request ending at `end` (tz-aware UTC). Retries on pacing errors.

    An empty frame means IB had no bars in that range (weekend, holiday); that is not an error.
    """
    spec = BAR_SIZES[bar_size]
    if end.tzinfo is None:
        raise ValueError("end must be timezone-aware")  # a naive time would be read as local time
    key = (contract.conId, end, spec.chunk_duration, spec.ib_setting, WHAT_TO_SHOW, USE_RTH)

    for attempt in range(len(RETRY_BACKOFF) + 1):
        throttle.wait(ib, key)
        ib.sleep(MIN_REQUEST_GAP)
        errors = []
        handler = lambda reqId, code, text, c=None: errors.append((code, text))  # noqa: E731
        ib.errorEvent += handler
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end,
                durationStr=spec.chunk_duration,
                barSizeSetting=spec.ib_setting,
                whatToShow=WHAT_TO_SHOW,
                useRTH=USE_RTH,
                formatDate=2,  # timestamps as UTC
            )
        finally:
            ib.errorEvent -= handler

        kind, message = _classify(errors)
        if kind == "pacing":
            if attempt == len(RETRY_BACKOFF):
                raise PacingError(f"{contract.symbol}: still pacing-limited after retries: {message}")
            wait = RETRY_BACKOFF[attempt]
            print(f"    pacing error ({message}); waiting {wait}s then retrying")
            ib.sleep(wait)
            continue
        if kind == "fatal" and not bars:
            raise RuntimeError(f"{contract.symbol}: {message}")
        if not bars:
            return pd.DataFrame()
        df = util.df(bars).set_index("date")
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
        return df
    raise AssertionError("unreachable")


def _session_dates(index):
    return set(index.tz_convert(SESSION_TZ).date)


def download_missing(
    ib, symbol, bar_size, cache_dir=CACHE_DIR, now=None, throttle: Throttle = hist_throttle
) -> dict:
    """Fetch only bars newer than the cache (or the seed window on a first run) and merge them in.

    Walks backwards from now in chunks. Nothing is written unless the whole walk succeeds,
    so a failure can never leave a hole between old and new data.
    """
    spec = BAR_SIZES[bar_size]
    now = now or datetime.now(timezone.utc)
    path = cache.cache_path(symbol, bar_size, cache_dir)
    last = cache.last_timestamp(path)

    trading_days = None
    if last is not None:
        start = last + spec.length  # only bars newer than the cache
    elif spec.seed_in_trading_days:
        start, trading_days = now - MAX_TRADING_DAY_WALK, spec.seed_days
    else:
        start = now - timedelta(days=spec.seed_days)

    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)

    frames, sessions, end = [], set(), now
    while end > start:
        df = fetch_chunk(ib, contract, end, bar_size, throttle)
        next_end = end - spec.chunk_length
        if not df.empty:
            frames.append(df)
            sessions |= _session_dates(df.index)
            oldest = df.index.min().to_pydatetime()
            if oldest < end:  # otherwise no progress: fall back to stepping a whole chunk
                next_end = oldest
        end = next_end
        if trading_days and len(sessions) >= trading_days:
            break

    new = pd.concat(frames) if frames else pd.DataFrame()
    if not new.empty:
        new = new[~new.index.duplicated(keep="last")].sort_index()
        new = new[new.index >= start]
        new = new[new.index + spec.length <= now]  # drop the bar still forming
        if trading_days:
            keep = sorted(_session_dates(new.index))[-trading_days:]
            new = new[[d in keep for d in new.index.tz_convert(SESSION_TZ).date]]

    before = 0 if last is None else len(cache.read(path))
    if new.empty:
        return {"path": path, "added": 0, "first": last, "last": last}
    merged = cache.merge_and_write(path, new)
    return {"path": path, "added": len(merged) - before, "first": merged.index.min(), "last": merged.index.max()}
