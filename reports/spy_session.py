"""Interactive chart of the latest full SPY session -> reports/spy_session.html.

5-minute candles, session VWAP, 20-bar EMA, volume, and markers where the close
crosses VWAP. The markers are an illustration only, not a trading signal.
Run: uv run -m reports.spy_session
"""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from indicators import ema, vwap

ROOT = Path(__file__).resolve().parent.parent
TZ = "America/New_York"
FULL_SESSION_BARS = 78  # 6.5 hours of regular trading / 5 minutes

bars = pd.read_parquet(ROOT / "data/cache/SPY_5min.parquet")
bars.index = pd.to_datetime(bars.index, utc=True)
bars["vwap"] = vwap(bars.high, bars.low, bars.close, bars.volume)
bars["ema20"] = ema(bars.close, 20)  # computed across days so it is warm at the open

day = bars.index.tz_convert(TZ).date
counts = pd.Series(day).value_counts()
session = max(d for d, n in counts.items() if n == FULL_SESSION_BARS)
df = bars[day == session].copy()
df.index = df.index.tz_convert(TZ).tz_localize(None)

# Cross = close on the other side of VWAP than the previous bar's close.
side = (df.close > df.vwap).astype(int) - (df.close < df.vwap).astype(int)
prev = side.shift().replace(0, pd.NA).ffill().fillna(0)  # last non-zero side
up = df[(side == 1) & (prev == -1)]
down = df[(side == -1) & (prev == 1)]

UP, DOWN = "#0b8a6f", "#d1483a"
VWAP_C, EMA_C = "#2b6cb0", "#c47f00"
fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22],
                    vertical_spacing=0.03)
fig.add_trace(go.Candlestick(
    x=df.index, open=df.open, high=df.high, low=df.low, close=df.close, name="SPY 5-min",
    increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
    decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN)), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df.vwap, name="VWAP", mode="lines",
                         line=dict(color=VWAP_C, width=2)), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df.ema20, name="EMA 20", mode="lines",
                         line=dict(color=EMA_C, width=2)), row=1, col=1)
for pts, name, sym, c, off in [(up, "Close crosses above VWAP", "triangle-up", UP, -1),
                               (down, "Close crosses below VWAP", "triangle-down", DOWN, 1)]:
    fig.add_trace(go.Scatter(
        x=pts.index, y=pts.low * (1 - 0.0004) if off < 0 else pts.high * (1 + 0.0004),
        mode="markers", name=name, customdata=pts.close,
        marker=dict(symbol=sym, size=9, color=c, line=dict(color="white", width=1)),
        hovertemplate="%{x|%H:%M} close %{customdata:.2f}<extra>" + name + "</extra>"),
        row=1, col=1)
fig.add_trace(go.Bar(x=df.index, y=df.volume, name="Volume", showlegend=False,
                     marker_color=[UP if c >= o else DOWN for o, c in zip(df.open, df.close)],
                     marker_opacity=0.6), row=2, col=1)

fig.update_layout(
    title=f"SPY 5-minute bars, {session:%a %d %b %Y} (illustration only, not a strategy)",
    template="plotly_white", hovermode="x unified", height=760,
    xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.06, x=0),
    margin=dict(l=60, r=30, t=90, b=40))
fig.update_yaxes(title_text="Price ($)", row=1, col=1)
fig.update_yaxes(title_text="Shares", row=2, col=1)
fig.update_xaxes(tickformat="%H:%M", title_text="Time (New York)", row=2, col=1)

out = ROOT / "reports/spy_session.html"
fig.write_html(out, include_plotlyjs=True)  # embedded, works offline
print(f"{session}: {len(df)} bars, {len(up)} up-crosses, {len(down)} down-crosses -> {out}")
