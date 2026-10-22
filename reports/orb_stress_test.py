"""Stress test of opening_range_breakout on QQQ 5min (15-minute range, 1R target).

1. Monte Carlo: take the real trades, draw them at random with replacement (5,000 times) and
   chain each draw into its own equity curve. Each trade counts as its % gain or loss of the
   account just before entry, so the draws compound the way the real account did.
2. Cost sweep: rerun the backtest with commission, spread and slippage at 0, 1, 2, 3 and 5
   times the usual, then bisect for the cost multiple where net profit hits zero (break-even).

Writes reports/orb_stress_test.html (one figure, three panels) and prints the numbers.
Run: uv run -m reports.orb_stress_test   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import compute_metrics
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

ROOT = Path(__file__).resolve().parent.parent
SYMBOL, RANGE_MINUTES, TARGET_R = "QQQ", 15, 1.0
N_DRAWS, SEED = 5_000, 42
MULTIPLES = [0, 1, 2, 3, 5]
USUAL = CostModel()
BLUE, ORANGE, RED, GREY = "#2a78d6", "#eb6834", "#d03b3b", "#888888"


def scaled(k: float) -> CostModel:
    return CostModel(commission_per_share=USUAL.commission_per_share * k,
                     spread=USUAL.spread * k, slippage_per_share=USUAL.slippage_per_share * k)


def backtest(bars: pd.DataFrame, k: float):
    params = OpeningRangeBreakoutParams(range_minutes=RANGE_MINUTES, target_r=TARGET_R)
    return run_backtest(bars, OpeningRangeBreakout(params), costs=scaled(k))


def trade_returns(res) -> np.ndarray:
    """Each trade's net P&L as a fraction of the account value just before it opened."""
    t = res.trades
    before = res.equity.shift(1).bfill().reindex(t.entry_time).to_numpy()
    return (t.net_pnl.to_numpy() / before)


def max_drawdown(curves: np.ndarray) -> np.ndarray:
    """Worst peak-to-trough fall (negative fraction) of each row of a (draws, trades) array."""
    peak = np.maximum.accumulate(curves, axis=1)
    return (curves / peak - 1).min(axis=1)


def monte_carlo(rets: np.ndarray) -> dict:
    rng = np.random.default_rng(SEED)
    draws = rng.choice(rets, size=(N_DRAWS, len(rets)), replace=True)
    curves = np.cumprod(1 + draws, axis=1)
    start = np.ones((N_DRAWS, 1))
    final = curves[:, -1] - 1
    dd = max_drawdown(np.hstack([start, curves]))
    real_curve = np.cumprod(1 + rets)
    return {"final": final, "dd": dd,
            "real_final": real_curve[-1] - 1,
            "real_dd": float(max_drawdown(np.hstack([[1.0], real_curve])[None, :])[0])}


def pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q))


def cost_sweep(bars: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for k in MULTIPLES:
        res = backtest(bars, k)
        m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
        rows.append({"k": k, "net_profit": m["net_profit"], "cagr": m["cagr"], "sharpe": m["sharpe"],
                     "max_drawdown": m["max_drawdown"], "profit_factor": m["profit_factor"],
                     "trades": m["trades"], "costs": float(res.trades.costs.sum())})
        print(f"  costs x{k}: net ${m['net_profit']:,.0f}, Sharpe {m['sharpe']:.2f}", flush=True)
    return pd.DataFrame(rows)


def break_even(bars: pd.DataFrame, sweep: pd.DataFrame) -> float | None:
    """Cost multiple where net profit crosses zero: bracket from the sweep, then bisect."""
    neg = sweep[sweep.net_profit <= 0]
    if neg.empty:  # still profitable at the highest multiple tested: keep doubling the cost
        lo = float(sweep.k.max())
        hi = lo * 2
        while hi <= 40 and backtest(bars, hi).trades.net_pnl.sum() > 0:
            lo, hi = hi, hi * 2
        if hi > 40:
            return None
    else:
        hi = float(neg.k.iloc[0])
        lo = float(sweep[sweep.k < hi].k.iloc[-1]) if (sweep.k < hi).any() else 0.0
    for _ in range(8):
        mid = (lo + hi) / 2
        net = backtest(bars, mid).trades.net_pnl.sum()
        lo, hi = (mid, hi) if net > 0 else (lo, mid)
    return (lo + hi) / 2


def chart(mc: dict, sweep: pd.DataFrame, be: float | None) -> go.Figure:
    fig = make_subplots(rows=1, cols=3, horizontal_spacing=0.08, subplot_titles=(
        "Final return, 5,000 reshuffles", "Max drawdown, 5,000 reshuffles",
        "Net profit vs. cost multiple"))
    for col, (key, real, color) in enumerate(
            [("final", mc["real_final"], BLUE), ("dd", mc["real_dd"], ORANGE)], start=1):
        v = mc[key] * 100
        fig.add_trace(go.Histogram(x=v, nbinsx=60, marker_color=color, opacity=0.75,
                                   showlegend=False, hovertemplate="%{x:.0f}%<br>%{y} curves<extra></extra>"),
                      row=1, col=col)
        for q in (5, 95):
            fig.add_vline(x=pct(v, q), line_dash="dash", line_color=GREY, row=1, col=col,
                          annotation_text=f"P{q}: {pct(v, q):.0f}%", annotation_position="top")
        fig.add_vline(x=real * 100, line_color=RED, line_width=2, row=1, col=col,
                      annotation_text=f"real: {real * 100:.0f}%", annotation_position="bottom")
        fig.update_xaxes(ticksuffix="%", row=1, col=col)
    fig.add_trace(go.Scatter(x=sweep.k, y=sweep.net_profit, mode="lines+markers", showlegend=False,
                             line=dict(color=BLUE, width=2),
                             hovertemplate="x%{x} costs<br>$%{y:,.0f}<extra></extra>"), row=1, col=3)
    fig.add_hline(y=0, line_color=GREY, line_width=1, row=1, col=3)
    if be is not None:
        fig.add_vline(x=be, line_dash="dash", line_color=RED, row=1, col=3,
                      annotation_text=f"break-even x{be:.1f}", annotation_position="top")
    fig.update_xaxes(title_text="Costs as a multiple of usual", row=1, col=3)
    fig.update_yaxes(tickprefix="$", tickformat=",.0f", row=1, col=3)
    fig.update_layout(template="plotly_white", height=460, margin=dict(l=60, r=30, t=70, b=50),
                      title=f"Opening range breakout, {SYMBOL} 5min, {RANGE_MINUTES}-min range, {TARGET_R:g}R target")
    return fig


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    print("Real backtest at usual costs...", flush=True)
    base = backtest(bars, 1)
    rets = trade_returns(base)
    m = compute_metrics(base.trades, base.equity, STARTING_EQUITY)
    print(f"  {len(rets)} trades, net ${m['net_profit']:,.0f}, max drawdown {m['max_drawdown']:.1%}, "
          f"win rate {(rets > 0).mean():.1%}, mean trade {rets.mean():.3%}")

    mc = monte_carlo(rets)
    print(f"\nMonte Carlo ({N_DRAWS:,} draws of {len(rets)} trades, with replacement)")
    for name, key, real in [("Final return", "final", mc["real_final"]),
                            ("Max drawdown", "dd", mc["real_dd"])]:
        a = mc[key]
        print(f"  {name}: P5 {pct(a, 5):.1%} | median {pct(a, 50):.1%} | P95 {pct(a, 95):.1%} | real {real:.1%}")
    print(f"  Draws that lose money: {(mc['final'] < 0).mean():.1%}; "
          f"draws with drawdown worse than 20%: {(mc['dd'] < -0.20).mean():.1%}")

    print("\nCost sweep", flush=True)
    sweep = cost_sweep(bars)
    be = break_even(bars, sweep)
    print(sweep.round(3).to_string(index=False))
    print(f"Break-even cost multiple: {'above 5x' if be is None else f'{be:.2f}x'}")

    out = ROOT / "reports" / "orb_stress_test.html"
    chart(mc, sweep, be).write_html(out, include_plotlyjs="cdn")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
