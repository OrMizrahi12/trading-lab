"""HTML report for the spy_vwap_reclaim backtest -> reports/spy_vwap_reclaim.html.

Metrics, equity curve, drawdown chart, monthly returns table and the full trade list,
from `backtests.engine.run_backtest` on SPY 5min (about a minute to run).
Run: uv run -m reports.spy_vwap_reclaim

Paper research only. Nothing here is financial advice.
"""
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

import indicators
from backtests.engine import RISK_PCT, STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import METRICS, compute_metrics, drawdown, format_value
from backtests.vectorized import CACHE, load_bars
from strategies.spy_vwap_reclaim import SpyVwapReclaim

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports/spy_vwap_reclaim.html"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
UP, DOWN, LINE = "#0b8a6f", "#d1483a", "#2b6cb0"

CSS = """
body{font:15px/1.5 system-ui,sans-serif;color:#1c2330;background:#fff;margin:0}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:18px;margin:36px 0 10px}
.sub,.note{color:#5b6573;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin-top:16px}
.card{border:1px solid #dde2ea;border-radius:8px;padding:12px 14px}
.card .label{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#5b6573}
.card .value{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.card .why{font-size:12px;color:#5b6573;margin-top:2px}
.neg{color:#d1483a} .pos{color:#0b8a6f}
.scroll{overflow-x:auto;border:1px solid #dde2ea;border-radius:8px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:5px 9px;text-align:right;white-space:nowrap} th:first-child,td:first-child{text-align:left}
th{background:#f4f6f9;position:sticky;top:0} tr+tr td{border-top:1px solid #eef0f4}
.trades{max-height:560px;overflow-y:auto}
@media (max-width:600px){.card .value{font-size:22px}}
"""


def sign_class(x: float) -> str:
    return "pos" if x > 0 else "neg" if x < 0 else ""


def cards(m: dict) -> str:
    out = []
    for metric in METRICS:
        v = m[metric.key]
        cls = sign_class(v) if metric.key in ("net_profit", "cagr", "expectancy", "avg_r") else ""
        out.append(
            f'<div class="card"><div class="label">{metric.label}</div>'
            f'<div class="value {cls}">{format_value(metric, v)}</div>'
            f'<div class="why">{escape(metric.explanation)}</div></div>'
        )
    return '<div class="cards">' + "".join(out) + "</div>"


def daily_equity(equity: pd.Series) -> pd.Series:
    day = equity.index.tz_convert(indicators.SESSION_TZ).date
    out = equity.groupby(day).last()
    out.index = pd.to_datetime(out.index)
    return out


def equity_fig(daily: pd.Series) -> go.Figure:
    fig = go.Figure(go.Scatter(x=daily.index, y=daily, mode="lines", name="Equity",
                               line=dict(color=LINE, width=2),
                               hovertemplate="%{x|%d %b %Y}<br>$%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=STARTING_EQUITY, line=dict(color="#9aa3af", width=1, dash="dot"))
    fig.update_yaxes(title_text="Account value ($)", tickprefix="$", tickformat=",.0f")
    return fig


def drawdown_fig(equity: pd.Series) -> go.Figure:
    dd = drawdown(equity, STARTING_EQUITY)
    day = dd.index.tz_convert(indicators.SESSION_TZ).date
    worst = dd.groupby(day).min() * 100  # deepest point of each day
    worst.index = pd.to_datetime(worst.index)
    fig = go.Figure(go.Scatter(x=worst.index, y=worst, mode="lines", name="Drawdown", fill="tozeroy",
                               line=dict(color=DOWN, width=1.5),
                               hovertemplate="%{x|%d %b %Y}<br>%{y:.2f}%<extra></extra>"))
    fig.update_yaxes(title_text="Below previous peak (%)", ticksuffix="%")
    return fig


def style(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(title=title, template="plotly_white", height=height, showlegend=False,
                      hovermode="x unified", margin=dict(l=70, r=20, t=50, b=40))
    return fig


def monthly_table(daily: pd.Series) -> str:
    month_end = daily.groupby(daily.index.to_period("M")).last()
    prev = month_end.shift(1)
    prev.iloc[0] = STARTING_EQUITY
    ret = month_end / prev - 1
    year_end = daily.groupby(daily.index.year).last()
    year_prev = year_end.shift(1)
    year_prev.iloc[0] = STARTING_EQUITY
    year_ret = year_end / year_prev - 1

    def cell(x: float | None) -> str:
        if x is None or pd.isna(x):
            return "<td></td>"
        a = min(abs(x) / 0.03, 1) * 0.55  # colour saturates at a 3% month
        rgb = "11,138,111" if x > 0 else "209,72,58"
        return f'<td style="background:rgba({rgb},{a:.2f})">{x:.2%}</td>'

    head = "".join(f"<th>{m}</th>" for m in MONTHS)
    rows = []
    for year in sorted(year_end.index):
        cells = "".join(cell(ret.get(pd.Period(year=year, month=m, freq="M"))) for m in range(1, 13))
        rows.append(f"<tr><td>{year}</td>{cells}<td><b>{year_ret[year]:.2%}</b></td></tr>")
    return (f'<div class="scroll"><table><tr><th>Year</th>{head}<th>Year</th></tr>'
            + "".join(rows) + "</table></div>")


def trades_table(trades: pd.DataFrame) -> str:
    t = trades.copy()
    for c in ("entry_time", "exit_time"):
        t[c] = t[c].dt.tz_convert(indicators.SESSION_TZ).dt.strftime("%Y-%m-%d %H:%M")
    head = ["#", "Entry (NY)", "Exit (NY)", "Shares", "Entry $", "Exit $", "Exit reason",
            "Gross P&L", "Costs", "Net P&L", "R"]
    rows = []
    for n, r in enumerate(t.itertuples(), 1):
        cls = sign_class(r.net_pnl)
        rows.append(
            f"<tr><td>{n}</td><td>{r.entry_time}</td><td>{r.exit_time}</td><td>{r.shares:,}</td>"
            f"<td>{r.entry_price:.2f}</td><td>{r.exit_price:.2f}</td><td>{escape(str(r.reason))}</td>"
            f"<td>{r.gross_pnl:,.2f}</td><td>{r.costs:,.2f}</td>"
            f'<td class="{cls}">{r.net_pnl:,.2f}</td><td class="{cls}">{r.r_multiple:.2f}</td></tr>'
        )
    return ('<div class="scroll trades"><table><tr>' + "".join(f"<th>{h}</th>" for h in head)
            + "</tr>" + "".join(rows) + "</table></div>")


def main() -> None:
    bars = load_bars(CACHE)
    result = run_backtest(bars, SpyVwapReclaim())
    metrics = compute_metrics(result.trades, result.equity, STARTING_EQUITY)
    daily = daily_equity(result.equity)
    costs = CostModel()
    span = f"{bars.index[0]:%d %b %Y} to {bars.index[-1]:%d %b %Y}"

    eq = style(equity_fig(daily), "Equity curve")
    dd = style(drawdown_fig(result.equity), "Drawdown", height=300)
    # Plotly's JS is embedded once (first chart) so the file works offline.
    eq_html = eq.to_html(full_html=False, include_plotlyjs=True)
    dd_html = dd.to_html(full_html=False, include_plotlyjs=False)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>spy_vwap_reclaim backtest</title><style>{CSS}</style></head>
<body><main>
<h1>spy_vwap_reclaim backtest</h1>
<div class="sub">SPY 5-minute bars, {span}. ${STARTING_EQUITY:,.0f} start, {RISK_PCT:.0%} of equity risked per trade.
Costs per share: {costs.commission_per_share:.2f} commission per side, {costs.spread:.2f} spread, {costs.slippage_per_share:.2f} slippage on market fills.
Paper research only, not financial advice.</div>
{cards(metrics)}
<h2>Equity curve</h2>{eq_html}
<h2>Drawdown</h2>{dd_html}
<h2>Monthly returns</h2>{monthly_table(daily)}
<p class="note">Each cell is the change in account value over that calendar month.</p>
<h2>Trades ({len(result.trades)})</h2>{trades_table(result.trades)}
</main></body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"{len(result.trades)} trades -> {OUT}")


if __name__ == "__main__":
    main()
