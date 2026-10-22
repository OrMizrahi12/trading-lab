"""IB market scans on the paper Gateway, plus a gap scanner for the open. Paper trading only.

Gap scanner: IB's HIGH_OPEN_GAP / LOW_OPEN_GAP scans (a gap = today's open differs from yesterday's
close) give candidates; our own bars from the Gateway give the real numbers and the filters.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from ib_async import IB, ScannerSubscription, util

from indicators import atr
from market_data import DELAYED, HOST, PORT, hist_throttle

CLIENT_ID = 4  # Must be unique per connection to the Gateway (1-3 are taken).

SESSION_TZ = "America/New_York"
WATCHLIST_DIR = Path(__file__).parent / "watchlists"

GAP_SCANS = ("HIGH_OPEN_GAP", "LOW_OPEN_GAP")  # gapped up / gapped down at the open
SCAN_ROWS = 50  # most rows IB returns per scan
HIST_DURATION, HIST_BAR = "1 M", "5 mins"  # about 21 sessions of regular-hours 5-minute bars
ATR_DAYS = 14  # ATR (average true range = a typical day's price travel) window, in daily bars
VOLUME_DAYS = 20  # days averaged for dollar volume and relative volume
MIN_PRICE = 5.0
SCAN_MIN_MARKET_CAP_M = 300  # scan-side prefilter, in $ millions: saves history requests (IB allows ~55 per 10 min)
MIN_AVG_DOLLAR_VOLUME = 20_000_000
MIN_GAP_PCT = 2.0
MIN_GAP_ATR = 1.0
PACING_CODE = 162  # IB code for historical data errors; only the ones that mention pacing count (see is_pacing)


def show_errors(reqId, errorCode, errorString, contract):
    """Print every message IB sends back, verbatim."""
    print(f"    IB message: reqId={reqId} code={errorCode} {errorString!r}")


def top_gainers(ib, rows=10):
    """TOP_PERC_GAIN = biggest percentage price gain today. Returns IB's ScanData rows."""
    sub = ScannerSubscription(
        instrument="STK",
        locationCode="STK.US.MAJOR",  # major US exchanges
        scanCode="TOP_PERC_GAIN",
        abovePrice=5,  # skip stocks under $5
        aboveVolume=1_000_000,  # skip stocks that traded under 1 million shares today
        numberOfRows=rows,
    )
    return ib.reqScannerData(sub)


def gap_candidates(ib, rows=SCAN_ROWS):
    """Contracts from both gap scans: US corporate stocks (no ETFs) over $5. Returns {symbol: (contract, scan)}."""
    found = {}
    for code in GAP_SCANS:
        sub = ScannerSubscription(
            instrument="STK",
            locationCode="STK.US.MAJOR",  # major US exchanges
            scanCode=code,
            abovePrice=MIN_PRICE,
            marketCapAbove=SCAN_MIN_MARKET_CAP_M,  # IB reads this in millions; a stock trading $20M a day is rarely smaller
            stockTypeFilter="CORP",  # corporate stocks only: IB's way to leave out ETFs
            numberOfRows=rows,
        )
        for r in ib.reqScannerData(sub):
            c = r.contractDetails.contract
            c.exchange = "SMART"  # route data requests through IB's smart router, not the listing venue
            found.setdefault(c.symbol, (c, code))
    return found


def gap_stats(bars, today):
    """Real gap numbers for one stock from its 5-minute regular-hours bars (UTC index), or a skip reason (str).

    Gap % = today's open vs yesterday's close. Gap in ATRs = the same gap in units of the 14-day ATR of
    earlier sessions. Relative volume (time of day) = shares traded so far today / the average shares traded
    by the same time on the previous 20 sessions. Today's last bar may still be forming, so it is left out
    of the volume comparison on both sides. Average dollar volume = price x shares per day, 20-session mean.
    """
    if bars is None or len(bars) == 0:
        return "no bars"
    df = bars.copy()
    ny = df.index.tz_convert(SESSION_TZ)
    df["day"] = ny.date
    df["dollars"] = df["close"] * df["volume"]
    days = sorted(df["day"].unique())
    if days[-1] != today:
        return f"no bars for {today} (last session {days[-1]})"
    today_bars = df[df["day"] == today]
    prior = df[df["day"] < today]
    daily = prior.groupby("day").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"), dollars=("dollars", "sum")
    )
    if len(daily) < ATR_DAYS + 1:
        return f"only {len(daily)} earlier sessions, need {ATR_DAYS + 1}"
    day_atr = atr(daily["high"], daily["low"], daily["close"], ATR_DAYS).iloc[-1]
    prev_close, open_ = daily["close"].iloc[-1], today_bars["open"].iloc[0]
    if not day_atr > 0 or prev_close <= 0:
        return "ATR or previous close not usable"

    k = len(today_bars) - 1  # completed bars today
    rel_vol = float("nan")
    if k > 0:
        so_far = today_bars["volume"].iloc[:k].sum()
        same_time = [g["volume"].iloc[:k].sum() for _, g in prior.groupby("day") if len(g) >= k][-VOLUME_DAYS:]
        if same_time and sum(same_time) > 0:
            rel_vol = so_far / (sum(same_time) / len(same_time))

    return {
        "gap_pct": (open_ / prev_close - 1) * 100,
        "gap_atr": (open_ - prev_close) / day_atr,
        "rel_vol": rel_vol,
        "price": today_bars["close"].iloc[-1],
        "avg_dollar_vol": daily["dollars"].tail(VOLUME_DAYS).mean(),
        "last_bar": ny[df["day"].to_numpy() == today][-1],  # start of the newest bar, New York time
    }


def passes_filters(row):
    """Price above $5, at least $20M average daily dollar volume, and a gap of 2% or 1 ATR (either direction)."""
    return bool(
        row["price"] > MIN_PRICE
        and row["avg_dollar_vol"] >= MIN_AVG_DOLLAR_VOLUME
        and (abs(row["gap_pct"]) >= MIN_GAP_PCT or abs(row["gap_atr"]) >= MIN_GAP_ATR)
    )


def rank_by_rel_vol(table):
    """Highest relative volume first; stocks with no relative volume yet go last."""
    return table.sort_values("rel_vol", ascending=False, na_position="last", kind="stable").reset_index(drop=True)


def is_pacing(error):
    """True for IB's "too many historical requests" error. Code 162 is also used for harmless notices
    (for example "API scanner subscription cancelled"), so the text has to say pacing."""
    code, text = error
    return code == PACING_CODE and "pacing" in text.lower()


def fetch_bars(ib, contract, throttle=hist_throttle):
    """Recent 5-minute regular-hours bars for one contract. Returns (DataFrame or None, list of IB errors)."""
    errors = []
    handler = lambda reqId, code, text, c=None: errors.append((code, text))  # noqa: E731
    throttle.wait(ib, (contract.symbol, HIST_BAR, HIST_DURATION))
    ib.errorEvent += handler
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=HIST_DURATION,
            barSizeSetting=HIST_BAR,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,  # UTC timestamps
        )
    finally:
        ib.errorEvent -= handler
    if not bars:
        return None, errors
    df = util.df(bars).set_index("date")
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    return df, errors


def run_gap_scan(ib, today=None, throttle=hist_throttle, rows=SCAN_ROWS):
    """Scan, measure, filter, rank. Returns (ranked passing table, all measured table, {symbol: why skipped})."""
    today = today or datetime.now(ZoneInfo(SESSION_TZ)).date()
    candidates = gap_candidates(ib, rows)
    measured, skipped = [], {}
    for n, (symbol, (contract, scan)) in enumerate(candidates.items(), 1):
        print(f"  [{n}/{len(candidates)}] {symbol}", flush=True)
        bars, errors = fetch_bars(ib, contract, throttle)
        if any(is_pacing(e) for e in errors):
            skipped[symbol] = "IB pacing violation; stopped asking for more bars"
            skipped.update({s: "not requested (pacing)" for s in list(candidates)[list(candidates).index(symbol) + 1 :]})
            break
        stats = gap_stats(bars, today)
        if isinstance(stats, str):
            skipped[symbol] = stats + (f" ({errors[0][1]})" if errors and not isinstance(stats, dict) else "")
            continue
        measured.append({"symbol": symbol, "scan": scan, **stats})
    columns = ["symbol", "scan", "gap_pct", "gap_atr", "rel_vol", "price", "avg_dollar_vol", "last_bar"]
    all_rows = pd.DataFrame(measured, columns=columns)
    keep = all_rows[all_rows.apply(passes_filters, axis=1)] if len(all_rows) else all_rows
    return rank_by_rel_vol(keep), all_rows, skipped


def save_watchlist(table, today, directory=WATCHLIST_DIR):
    """Write the list to watchlists/gap_scan_YYYY-MM-DD.csv (a rerun on the same day replaces the file)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"gap_scan_{today.isoformat()}.csv"
    table.to_csv(path, index=False)
    return path


def format_table(table):
    """Plain-text table for the terminal."""
    if table.empty:
        return "(no stocks passed the filters)"
    out = pd.DataFrame(
        {
            "symbol": table["symbol"],
            "scan": table["scan"],
            "gap %": table["gap_pct"].map("{:+.2f}".format),
            "gap ATR": table["gap_atr"].map("{:+.2f}".format),
            "rel vol": table["rel_vol"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.2f}x"),
            "price": table["price"].map("{:.2f}".format),
            "avg $ vol (M)": (table["avg_dollar_vol"] / 1e6).map("{:,.0f}".format),
        }
    )
    out.index = range(1, len(out) + 1)
    return out.to_string()


def main():
    ib = IB()
    ib.errorEvent += show_errors
    # readonly=True: the session can read data but cannot place orders.
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)
    try:
        accounts = ib.managedAccounts()
        if not all(a.startswith("DU") for a in accounts):
            raise RuntimeError(f"Non-paper account(s): {accounts}")

        ib.reqMarketDataType(DELAYED)  # fall back to delayed data where live is not subscribed
        today = datetime.now(ZoneInfo(SESSION_TZ)).date()
        table, measured, skipped = run_gap_scan(ib, today)
        path = save_watchlist(table, today)
        print(f"\nGap scan {today}: {len(measured)} measured, {len(table)} passed "
              f"(market cap >= ${SCAN_MIN_MARKET_CAP_M}M, price > ${MIN_PRICE:.0f}, avg $ volume >= ${MIN_AVG_DOLLAR_VOLUME / 1e6:.0f}M, "
              f"gap >= {MIN_GAP_PCT}% or {MIN_GAP_ATR} ATR), ranked by relative volume")
        print(format_table(table))
        if len(measured):
            newest = measured["last_bar"].max()
            print(f"newest bar seen starts {newest:%H:%M} New York; now {datetime.now(ZoneInfo(SESSION_TZ)):%H:%M}")
        for symbol, why in skipped.items():
            print(f"  skipped {symbol}: {why}")
        print(f"saved {path}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
