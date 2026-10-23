"""Study chart of one stock's 5-minute bars for one session, with the previous close.

Pulls two sessions of regular-hours 5-minute bars from the IB Gateway (paper port, read-only),
draws the chosen session as candlesticks and a dashed line at the previous session's last
close, so the opening gap (the jump between yesterday's close and today's open) is easy to see.
Writes reports/<SYMBOL>_<DATE>_5min.html (gitignored) and prints its path.
Run: uv run -m reports.gap_chart HXL 2026-10-22 [--port 4002] [--open]   (IB client ID 9)

Paper research only. Nothing here places an order. A study chart, not a tip.
"""
import argparse
import datetime as dt
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go

NY = ZoneInfo("America/New_York")
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 9  # desk project holds 8, 20 and 30; see AGENTS.md
ROOT = Path(__file__).resolve().parent.parent


def split_session(bars: pd.DataFrame, day: dt.date) -> tuple[pd.DataFrame, float]:
    """The bars of `day` (New York time) and the last close of the session before it."""
    local = bars.assign(date=pd.to_datetime(bars["date"], utc=True).dt.tz_convert(NY))
    today = local[local["date"].dt.date == day]
    before = local[local["date"].dt.date < day]
    if today.empty:
        raise ValueError(f"no bars for {day} (weekend, holiday or no trading?)")
    if before.empty:
        raise ValueError(f"no earlier session found before {day}, so no previous close")
    return today, float(before.iloc[-1]["close"])


def build_figure(symbol: str, day: dt.date, today: pd.DataFrame, prev_close: float) -> go.Figure:
    fig = go.Figure(go.Candlestick(x=today["date"], open=today["open"], high=today["high"],
                                   low=today["low"], close=today["close"], name=f"{symbol} 5 min"))
    fig.add_hline(y=prev_close, line_dash="dash", line_color="gray",
                  annotation_text=f"Previous close {prev_close:.2f}", annotation_position="top left")
    fig.update_layout(title=f"{symbol}, 5-minute bars, {day} (regular hours) with previous close",
                      xaxis_rangeslider_visible=False, yaxis_title="Price (USD)",
                      xaxis_title="Time (New York)")
    return fig


def fetch_bars(symbol: str, day: dt.date, port: int) -> pd.DataFrame:
    from ib_async import IB, Stock

    if port not in PAPER_PORTS:
        raise RuntimeError(f"Refusing to connect: port {port} is not a paper port")
    ib = IB()
    ib.connect("127.0.0.1", port, clientId=CLIENT_ID, readonly=True)
    try:
        if not all(a.startswith("DU") for a in ib.managedAccounts()):
            raise RuntimeError("Refusing to continue: not a paper account (DU...)")
        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        end = dt.datetime.combine(day, dt.time(16, 0), tzinfo=NY)
        bars = ib.reqHistoricalData(contract, endDateTime=end, durationStr="2 D",
                                    barSizeSetting="5 mins", whatToShow="TRADES",
                                    useRTH=True, formatDate=2)
    finally:
        ib.disconnect()
    return pd.DataFrame([{"date": b.date, "open": b.open, "high": b.high, "low": b.low,
                          "close": b.close} for b in bars])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("symbol")
    p.add_argument("date", type=dt.date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--port", type=int, default=4002)
    p.add_argument("--open", action="store_true", help="open the file in Chrome afterwards")
    a = p.parse_args()
    symbol = a.symbol.upper()
    today, prev_close = split_session(fetch_bars(symbol, a.date, a.port), a.date)
    out = ROOT / "reports" / f"{symbol}_{a.date}_5min.html"
    build_figure(symbol, a.date, today, prev_close).write_html(out)
    print(f"{out}  ({len(today)} bars, previous close {prev_close:.2f}, open {today.iloc[0]['open']:.2f})")
    if a.open:
        subprocess.Popen(["google-chrome", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
