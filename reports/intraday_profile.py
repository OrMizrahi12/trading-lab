"""Shape of the trading day -> reports/intraday_profile.html.

For every 5-minute slot from 09:30 to 15:55 New York, the average absolute move
(% change from the bar's open to its close, ignoring direction) and the average
volume, over all cached SPY and QQQ days. Half days only have morning slots, so
their afternoon slots are simply missing from that slot's average.
Run: uv run -m reports.intraday_profile
"""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent.parent
TZ = "America/New_York"
SYMBOLS = {"SPY": "#2b6cb0", "QQQ": "#c47f00"}


def slot_profile(symbol: str) -> pd.DataFrame:
    bars = pd.read_parquet(ROOT / f"data/cache/{symbol}_5min.parquet")
    local = bars.index.tz_convert(TZ)
    bars = bars.assign(
        abs_move=(bars.close / bars.open - 1).abs() * 100,
        slot=local.strftime("%H:%M"),
    )
    prof = bars.groupby("slot").agg(abs_move=("abs_move", "mean"),
                                    volume=("volume", "mean"), days=("abs_move", "size"))
    return prof.loc["09:30":"15:55"]


profiles = {s: slot_profile(s) for s in SYMBOLS}

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                    subplot_titles=("Average absolute move per 5-minute bar (%)",
                                    "Average volume per 5-minute bar (shares)"))
for sym, color in SYMBOLS.items():
    p = profiles[sym]
    fig.add_trace(go.Scatter(
        x=p.index, y=p.abs_move, name=sym, legendgroup=sym, mode="lines",
        line=dict(color=color, width=2), customdata=p.days,
        hovertemplate="%{y:.3f}%<extra>" + sym + " move</extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=p.index, y=p.volume, name=sym, legendgroup=sym, showlegend=False, mode="lines",
        line=dict(color=color, width=2), customdata=p.days,
        hovertemplate="%{y:,.0f}<extra>" + sym + " volume</extra>"), row=2, col=1)

fig.update_layout(
    title="The shape of a trading day: SPY and QQQ, 09:30-15:55 New York "
          f"({profiles['SPY'].days.max()} sessions)",
    template="plotly_white", hovermode="x unified", height=780,
    legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=70, r=30, t=110, b=50))
fig.update_yaxes(title_text="% of price", rangemode="tozero", row=1, col=1)
fig.update_yaxes(title_text="Shares", rangemode="tozero", tickformat="~s", row=2, col=1)
fig.update_xaxes(title_text="Start of the 5-minute bar (New York time)",
                 tickmode="array", tickvals=[f"{h:02d}:{m:02d}" for h in range(9, 16)
                                             for m in (0, 30) if "09:30" <= f"{h:02d}:{m:02d}" <= "15:30"],
                 row=2, col=1)

out = ROOT / "reports/intraday_profile.html"
fig.write_html(out, include_plotlyjs=True)  # embedded, works offline
print(f"-> {out}")

# Numbers for the written summary.
for sym, p in profiles.items():
    print(f"\n{sym}: days per slot min/max {p.days.min()}/{p.days.max()}")
    print(p[["abs_move", "volume"]].round(4).iloc[[0, 1, 2, 5, 11, 23, 35, 47, 59, 71, 73, 74, 75]].to_string())
    print("quietest 3 by move:", p.abs_move.nsmallest(3).round(4).to_dict())
    print("busiest 3 by volume:", p.volume.nlargest(3).round(0).to_dict())
    print("quietest 3 by volume:", p.volume.nsmallest(3).round(0).to_dict())
    print("mean move 09:30-10:00:", round(p.abs_move.loc["09:30":"09:55"].mean(), 4),
          "| 10:00-11:30:", round(p.abs_move.loc["10:00":"11:25"].mean(), 4),
          "| 11:30-14:00:", round(p.abs_move.loc["11:30":"13:55"].mean(), 4),
          "| 14:00-15:30:", round(p.abs_move.loc["14:00":"15:25"].mean(), 4),
          "| 15:30-16:00:", round(p.abs_move.loc["15:30":"15:55"].mean(), 4))
    print("mean vol  same windows:", [round(p.volume.loc[a:b].mean()) for a, b in
          [("09:30", "09:55"), ("10:00", "11:25"), ("11:30", "13:55"), ("14:00", "15:25"), ("15:30", "15:55")]])
