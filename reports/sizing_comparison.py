"""Same trades, three position sizers -> reports/sizing_comparison.html.

Runs opening_range_breakout (QQQ 5min, 15-minute range, usual costs, no leverage) under
fixed fractional (1% risk), volatility sizing (a normal day's move costs 1%) and fractional
Kelly (half Kelly of past trades, capped at 2%), then shows one table and one equity chart.
The entries and exits are identical; only the share count differs.
Run: uv run -m reports.sizing_comparison   (about a minute)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import METRICS, compute_metrics, format_value
from backtests.sizing import FixedFractional, FractionalKelly, VolatilityTarget, kelly_fraction
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

ROOT = Path(__file__).resolve().parent.parent
SYMBOL, RANGE_MINUTES = "QQQ", 15
SIZERS = [FixedFractional(), VolatilityTarget(), FractionalKelly()]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]  # categorical slots 1-3 of the dataviz palette
COLUMNS = ["trades", "net_profit", "cagr", "sharpe", "max_drawdown", "profit_factor", "win_rate"]
LABELS = {m.key: m for m in METRICS}


def run_all() -> list[dict]:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    params = OpeningRangeBreakoutParams(range_minutes=RANGE_MINUTES)
    rows = []
    for sizer in SIZERS:
        res = run_backtest(bars, OpeningRangeBreakout(params), costs=CostModel(), sizer=sizer)
        t = res.trades
        # Account value just before each entry, to express size and risk as % of the account.
        before = res.equity.shift(1).bfill().reindex(t.entry_time).to_numpy()
        rows.append({
            "sizer": sizer.name, "result": res,
            "position_pct": float((t.shares * t.entry_price / before).mean()),
            "stop_risk_pct": float((t.stop_risk / before).mean()),
            "worst_trade_pct": float((t.net_pnl / before).min()),
            **compute_metrics(t, res.equity, STARTING_EQUITY),
        })
        print(f"done {sizer.name}", flush=True)
    return rows


def table_html(rows: list[dict]) -> str:
    extra = [("position_pct", "Avg position (% of account)"),
             ("stop_risk_pct", "Avg risk if stopped (% of account)"),
             ("worst_trade_pct", "Worst trade (% of account)")]
    head = ["Sizer"] + [LABELS[c].label for c in COLUMNS] + [e[1] for e in extra]
    out = ["<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in head) + "</tr></thead><tbody>"]
    for color, r in zip(COLORS, rows):
        cells = [f'<td><span class="dot" style="background:{color}"></span>{escape(r["sizer"])}</td>']
        cells += [f"<td>{format_value(LABELS[c], r[c])}</td>" for c in COLUMNS]
        cells += [f"<td>{r[k]:.1%}</td>" for k, _ in extra]
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(out) + "</tbody></table>"


def chart(rows: list[dict]) -> go.Figure:
    fig = go.Figure()
    for color, r in zip(COLORS, rows):
        eq = r["result"].equity
        daily = eq.groupby(eq.index.tz_convert("America/New_York").date).last()
        fig.add_trace(go.Scatter(x=list(daily.index), y=daily.values, name=r["sizer"],
                                 mode="lines", line=dict(color=color, width=2),
                                 hovertemplate="%{x}<br>$%{y:,.0f}<extra>" + r["sizer"] + "</extra>"))
    fig.add_hline(y=STARTING_EQUITY, line_color="#888", line_width=1, line_dash="dot")
    fig.update_layout(template="plotly_white", height=480, hovermode="x unified",
                      yaxis=dict(tickprefix="$", tickformat=",.0f", title="Account value"),
                      legend=dict(orientation="h", y=1.1, x=0),
                      margin=dict(l=70, r=30, t=40, b=40))
    return fig


def page(rows: list[dict], span: str) -> str:
    kelly_now = kelly_fraction(pd.Series(
        (rows[2]["result"].trades.net_pnl / rows[2]["result"].trades.stop_risk).to_numpy()))
    fig = chart(rows).to_html(full_html=False, include_plotlyjs=True)  # embedded, works offline
    return f"""<!doctype html><meta charset="utf-8"><title>Position sizing comparison</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;background:#fcfcfb;color:#0b0b0b;max-width:1200px;margin:24px auto;padding:0 16px}}
h1{{font-size:22px;margin:0 0 4px}} p{{color:#52514e;margin:4px 0 16px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin-bottom:24px}}
th,td{{text-align:right;padding:6px 10px;border-bottom:1px solid #e3e2de;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{font-weight:600;color:#52514e;white-space:normal}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
@media (max-width:700px){{.wrap{{overflow-x:auto}}}}
</style>
<h1>Position sizing: {SYMBOL} opening range breakout, {RANGE_MINUTES}-minute range</h1>
<p>5min bars {span}, ${STARTING_EQUITY:,.0f} start, usual costs, no leverage, long only, flat before
the close. Same entries and exits in all three rows; only the number of shares differs.
Full Kelly on all trades at the end: {kelly_now:.1%} of the account per trade (the sizer used half
of what it knew at each moment, capped at 2%).</p>
<div class="wrap">{table_html(rows)}</div>
{fig}
"""


def main() -> None:
    rows = run_all()
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    out = ROOT / "reports/sizing_comparison.html"
    out.write_text(page(rows, span), encoding="utf-8")
    print(f"\nreport -> {out}\n")
    for r in rows:
        print(f"{r['sizer']:<24} trades {r['trades']:>4}  net ${r['net_profit']:>9,.0f}  "
              f"CAGR {r['cagr']:>6.1%}  Sharpe {r['sharpe']:.2f}  maxDD {r['max_drawdown']:>6.1%}  "
              f"PF {r['profit_factor']:.2f}  pos {r['position_pct']:.0%}  "
              f"risk@stop {r['stop_risk_pct']:.2%}  worst {r['worst_trade_pct']:.2%}")


if __name__ == "__main__":
    main()
