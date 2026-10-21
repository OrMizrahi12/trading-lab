"""Incremental download of IB historical bars into the Parquet cache. Paper trading only."""

import time
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
    MAX_EMPTY_CHUNKS,
    MAX_TRADING_DAY_WALK,
    MIN_REQUEST_GAP,
    PORT,
    RETRY_BACKOFF,
    SESSION_TZ,
    USE_RTH,
    WHAT_TO_SHOW,
)

DELAYED = 3  # IB market data type: fall back to delayed data where live is not subscribed
HIST_TIMEOUT = 60  # ib_async's default wait for a historical reply, in seconds


class PacingError(RuntimeError):
    """IB kept rejecting requests for going too fast, even after backing off."""


def connect_paper() -> IB:
    """Connect read-only to the paper Gateway and refuse anything but a DU (paper) account."""
    ib = IB()
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)
        accounts = ib.managedAccounts()
        if not accounts or not all(a.startswith("DU") for a in accounts):
            raise RuntimeError(f"Non-paper account(s): {accounts}")
        ib.reqMarketDataType(DELAYED)
    except BaseException:
        ib.disconnect()
        raise
    return ib


# Farm status messages that are only informational (connection OK / farm inactive but usable).
INFORMATIONAL_CODES = {165, 2104, 2106, 2107, 2108, 2158}


def _classify(errors):
    """Return ("pacing"|"fatal"|"transient"|None, message) for the IB messages seen during one request.

    pacing: asked too fast. transient: a data farm dropped mid-request (2103, 2105, 2110, 2157...),
    so an empty or short answer cannot be trusted. fatal: anything else, e.g. no market data permission.
    """
    for code, text in errors:
        if code == 100 or "pacing violation" in text.lower():
            return "pacing", text
    transient = None
    for code, text in errors:
        if code in INFORMATIONAL_CODES or "no data" in text.lower():  # "HMDS query returned no data" = empty range
            continue
        if 2100 <= code < 2200:
            transient = transient or (f"IB message {code}: {text}")
        else:
            return "fatal", f"IB error {code}: {text}"
    return ("transient", transient) if transient else (None, "")


def fetch_chunk(ib, contract, end, bar_size, throttle: Throttle = hist_throttle) -> pd.DataFrame:
    """One historical request ending at `end` (tz-aware UTC). Retries on pacing errors and dropped farms.

    An empty frame means IB had no bars in that range (weekend, holiday); that is not an error.
    A request that timed out is *not* an empty range: ib_async returns an empty list for it without
    any error message, so a call that came back empty after the full timeout is retried, then raised.
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
        started = time.monotonic()
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
        if not bars and not errors and time.monotonic() - started >= HIST_TIMEOUT - 1:
            kind, message = "transient", f"request timed out after {HIST_TIMEOUT}s with no reply"
        if kind in ("pacing", "transient"):
            if attempt == len(RETRY_BACKOFF):
                error = PacingError if kind == "pacing" else RuntimeError
                raise error(f"{contract.symbol}: still failing after retries: {message}")
            wait = RETRY_BACKOFF[attempt]
            print(f"    {kind} problem ({message}); waiting {wait}s then retrying")
            ib.sleep(wait)
            continue
        if kind == "fatal":  # even if some bars came back, the answer cannot be trusted
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
        # One session more than asked: the oldest one seen is usually cut off mid-day by the chunk
        # boundary, so it is only a witness that the sessions after it are complete. It is dropped below.
        if trading_days and len(sessions) >= trading_days + 1:
            break

    new = pd.concat(frames) if frames else pd.DataFrame()
    if not new.empty:
        new = new[~new.index.duplicated(keep="last")].sort_index()
        new = new[new.index >= start]
        new = new[new.index + spec.length <= now]  # drop the bar still forming
        if trading_days:
            keep = sorted(_session_dates(new.index))[-trading_days:]
            new = new[[d in keep for d in new.index.tz_convert(SESSION_TZ).date]]

    old = cache.read(path)
    before = 0 if old is None else len(old)
    if new.empty:
        if old is None or old.empty:
            return {"path": path, "added": 0, "first": None, "last": None}
        return {"path": path, "added": 0, "first": old.index.min(), "last": old.index.max()}
    merged = cache.merge_and_write(path, new)
    return {"path": path, "added": len(merged) - before, "first": merged.index.min(), "last": merged.index.max()}


def backfill(
    ib, symbol, bar_size, target_start, cache_dir=CACHE_DIR, now=None, throttle: Throttle = hist_throttle, log=print
) -> dict:
    """Walk further back in time, one chunk per request, until the cache reaches `target_start`.

    Starts at the oldest cached bar (or now, if there is no cache) and writes every chunk to the cache
    as soon as it arrives. Each write extends the cache backwards with no hole, so if the run stops
    (pacing error, lost connection, Ctrl-C) the same command resumes where it left off.
    """
    spec = BAR_SIZES[bar_size]
    if target_start.tzinfo is None:
        raise ValueError("target_start must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    path = cache.cache_path(symbol, bar_size, cache_dir)
    old = cache.read(path)
    end = now if old is None or old.empty else old.index.min().to_pydatetime()
    added, requests, empty_run = 0, 0, 0

    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)

    while end > target_start:
        df = fetch_chunk(ib, contract, end, bar_size, throttle)
        requests += 1
        next_end = end - spec.chunk_length
        if df.empty:
            empty_run += 1
            if empty_run >= MAX_EMPTY_CHUNKS:
                log(f"    {symbol}: {empty_run} empty chunks in a row before {end:%Y-%m-%d}; no earlier data, stopping")
                break
        else:
            empty_run = 0
            oldest = df.index.min().to_pydatetime()
            new = df[(df.index >= target_start) & (df.index + spec.length <= now)]
            if not new.empty:
                before = len(cache.read(path)) if path.exists() else 0
                added += len(cache.merge_and_write(path, new)) - before
            if oldest < end:
                next_end = oldest
            if oldest <= target_start:
                end = target_start
                break
        end = next_end
        log(f"    {symbol} {bar_size}: reached {end:%Y-%m-%d}, +{added} bars so far")

    merged = cache.read(path)
    first, last = (None, None) if merged is None or merged.empty else (merged.index.min(), merged.index.max())
    return {"path": path, "added": added, "requests": requests, "first": first, "last": last}
