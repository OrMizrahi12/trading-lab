"""When does mean reversion stop working? -> reports/reversion_by_day_type.html.

Splits every trade of bollinger_rsi_reversion and vwap_stretch_reversion (SPY and QQQ 5min,
usual costs) by the type of day it was entered on. A trend day is one of the third of sessions
(per symbol) with the biggest absolute move from the day's open to its close; every other
session is "other". Compares trades, net profit, average R, win rate and stop-outs.
Run: uv run -m reports.reversion_by_day_type   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.vectorized import CACHE, load_bars
from strategies.bollinger_rsi_reversion import BollingerRsiReversion
from strategies.vwap_stretch_reversion import VwapStretchReversion

ROOT = Path(__file__).resolve().parent.parent
TZ = "America/New_York"
SYMBOLS = ["SPY", "QQQ"]
STRATEGIES = {
    "bollinger_rsi_reversion": BollingerRsiReversion,
    "vwap_stretch_reversion": VwapStretchReversion,
}
COLORS = {"Trend days": "#c0392b", "Other days": "#2b6cb0"}


def day_types(bars: pd.DataFrame) -> pd.Series:
    """Session date -> 'Trend days' (top third by |close/open - 1|) or 'Other days'."""
    local = bars.index.tz_convert(TZ)
    day = pd.Series(local.date, index=bars.index)
    g = bars.groupby(day.values)
    move = (g.close.last() / g.open.first() - 1).abs()
    cutoff = move.quantile(2 / 3)
    return move.ge(cutoff).map({True: "Trend days", False: "Other days"}), move


def summarize(t: pd.DataFrame) -> dict:
    n = len(t)
    if n == 0:
        return dict(trades=0, net_profit=0.0, avg_r=float("nan"), win_rate=float("nan"),
                    stop_outs=0, stop_rate=float("nan"), profit_factor=float("nan"))
    wins, losses = t.net_pnl[t.net_pnl > 0].sum(), -t.net_pnl[t.net_pnl < 0].sum()
    stops = int((t.reason == "stop").sum())
    return dict(trades=n, net_profit=t.net_pnl.sum(), avg_r=t.r_multiple.mean(),
                win_rate=(t.net_pnl > 0).mean(), stop_outs=stops, stop_rate=stops / n,
                profit_factor=wins / losses if losses else float("inf"))


def run_all() -> pd.DataFrame:
    rows = []
    for symbol in SYMBOLS:
        bars = load_bars(CACHE.with_name(f"{symbol}_5min.parquet"))
        types, move = day_types(bars)
        print(f"{symbol}: {len(types)} sessions, trend-day cutoff "
              f"{move.quantile(2 / 3):.2%} open-to-close move", flush=True)
        for name, cls in STRATEGIES.items():
            res = run_backtest(bars, cls(), costs=CostModel())
            t = res.trades.copy()
            t["day"] = t.entry_time.dt.tz_convert(TZ).dt.date.map(types)
            for label in COLORS:
                rows.append({"strategy": name, "symbol": symbol, "day_type": label,
                             "days": int((types == label).sum()),
                             **summarize(t[t.day == label])})
            print(f"done {name} {symbol}", flush=True)
    return pd.DataFrame(rows)


def chart(df: pd.DataFrame) -> go.Figure:
    combos = [f"{s}<br>{y}" for s in STRATEGIES for y in SYMBOLS]
    keys = [(s, y) for s in STRATEGIES for y in SYMBOLS]
    panels = [("avg_r", "Average R per trade (1R = 1% of equity)", ".3f"),
              ("net_profit", "Net profit ($)", ",.0f"),
              ("stop_rate", "Share of trades stopped out", ".0%")]
    fig = make_subplots(rows=1, cols=3, subplot_titles=[p[1] for p in panels],
                        horizontal_spacing=0.08)
    for ci, (col, _, fmt) in enumerate(panels, start=1):
        for label, color in COLORS.items():
            vals = [df[(df.strategy == s) & (df.symbol == y) & (df.day_type == label)][col].iloc[0]
                    for s, y in keys]
            fig.add_trace(go.Bar(
                x=combos, y=vals, name=label, legendgroup=label, showlegend=ci == 1,
                marker_color=color, hovertemplate="%{y:" + fmt + "}<extra>" + label + "</extra>"),
                row=1, col=ci)
    fig.add_hline(y=0, line_color="#444", line_width=1, row=1, col=1)
    fig.add_hline(y=0, line_color="#444", line_width=1, row=1, col=2)
    fig.update_yaxes(tickformat=".0%", row=1, col=3)
    fig.update_yaxes(tickprefix="$", tickformat="~s", row=1, col=2)
    fig.update_layout(
        title="Mean reversion on trend days vs. all other days (usual costs, long only)",
        template="plotly_white", barmode="group", height=520,
        legend=dict(orientation="h", y=1.15, x=0), margin=dict(l=60, r=30, t=120, b=60))
    return fig


def main() -> None:
    df = run_all()
    out = ROOT / "reports/reversion_by_day_type.html"
    chart(df).write_html(out, include_plotlyjs=True)  # embedded, works offline
    print(f"\nstarting equity ${STARTING_EQUITY:,.0f}; chart -> {out}\n")
    show = df.copy()
    show["win_rate"] = show.win_rate.map("{:.0%}".format)
    show["stop_rate"] = show.stop_rate.map("{:.0%}".format)
    show["net_profit"] = show.net_profit.map("{:,.0f}".format)
    show["avg_r"] = show.avg_r.map("{:.3f}".format)
    show["profit_factor"] = show.profit_factor.map("{:.2f}".format)
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
