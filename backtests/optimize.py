"""Grid search for opening_range_breakout on QQQ 5min: range length x profit target.

Range lengths 5, 10, 15, 20, 30, 45, 60 minutes against targets none, 1, 2, 3, 5, 10 R (one R
is the distance from the entry to the stop, the opening range low). That is 42 backtests through
the shared engine with usual costs, 1% risk per trade, no leverage, long only, flat before the
close. The objective is the Sharpe ratio (return per unit of day-to-day swing); a cell with fewer
than 100 trades is ignored because too few trades say nothing. The heatmap goes to
reports/optimize_orb_qqq.html, and the printout judges whether the best cell is a plateau (its
neighbours are almost as good, so the result does not hinge on one exact setting) or a lucky
peak (its neighbours are much worse, so it is probably noise).
Run: uv run -m backtests.optimize   (several minutes; cells run in parallel)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from backtests.engine import RISK_PCT, STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import compute_metrics
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "QQQ"
RANGES = [5, 10, 15, 20, 30, 45, 60]  # minutes
TARGETS = [None, 1, 2, 3, 5, 10]  # in R; None = no target
MIN_TRADES = 100
OUT = ROOT / "reports/optimize_orb_qqq.html"


def target_label(t) -> str:
    return "none" if t is None else f"{t}R"


def run_cell(args: tuple[int, float | None]) -> dict:
    minutes, target = args
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    params = OpeningRangeBreakoutParams(range_minutes=minutes, target_r=target)
    res = run_backtest(bars, OpeningRangeBreakout(params), costs=CostModel())
    m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
    return {"range": minutes, "target": target_label(target), **m}


def run_grid() -> pd.DataFrame:
    cells = [(r, t) for r in RANGES for t in TARGETS]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(run_cell, cells))
    return pd.DataFrame(rows)


def matrix(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Rows = range minutes, columns = target, in grid order."""
    return df.pivot(index="range", columns="target", values=col).loc[
        RANGES, [target_label(t) for t in TARGETS]]


def valid_sharpe(df: pd.DataFrame) -> pd.DataFrame:
    sh = matrix(df, "sharpe")
    return sh.where(matrix(df, "trades") >= MIN_TRADES)  # NaN = ignored cell


def neighbours(sh: pd.DataFrame, i: int, j: int) -> list[float]:
    """Sharpe of the up-to-8 adjacent cells that are not ignored."""
    out = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            a, b = i + di, j + dj
            if (di or dj) and 0 <= a < sh.shape[0] and 0 <= b < sh.shape[1] and not np.isnan(sh.iat[a, b]):
                out.append(float(sh.iat[a, b]))
    return out


def judge(sh: pd.DataFrame) -> dict:
    """Is the best cell a plateau or a peak? Compares it with its neighbours and the whole grid."""
    flat = sh.stack()
    best_loc = flat.idxmax()
    i, j = RANGES.index(best_loc[0]), list(sh.columns).index(best_loc[1])
    best = float(flat.max())
    nb = neighbours(sh, i, j)
    nb_mean = float(np.mean(nb)) if nb else float("nan")
    # Smoothed score: each cell averaged with its neighbours. A plateau scores high here, a lone spike does not.
    smooth = pd.DataFrame(
        [[np.mean([sh.iat[a, b]] + neighbours(sh, a, b)) if not np.isnan(sh.iat[a, b]) else np.nan
          for b in range(sh.shape[1])] for a in range(sh.shape[0])],
        index=sh.index, columns=sh.columns)
    sm_loc = smooth.stack().idxmax()
    return {
        "best_loc": best_loc, "best": best, "neighbours": nb, "nb_mean": nb_mean,
        "nb_min": min(nb) if nb else float("nan"), "nb_pct_of_best": nb_mean / best if best else float("nan"),
        "grid_median": float(flat.median()), "n_valid": int(flat.size), "n_positive": int((flat > 0).sum()),
        "smooth_loc": sm_loc, "smooth_best": float(smooth.stack().max()),
    }


def heatmap(df: pd.DataFrame, sh: pd.DataFrame, verdict: dict) -> go.Figure:
    trades = matrix(df, "trades")
    text = [[f"{sh.iat[a, b]:.2f}<br>{int(trades.iat[a, b])} tr" if not np.isnan(sh.iat[a, b])
             else f"ignored<br>{int(trades.iat[a, b])} tr" for b in range(sh.shape[1])]
            for a in range(sh.shape[0])]
    fig = go.Figure(go.Heatmap(
        z=sh.to_numpy(), x=list(sh.columns), y=[f"{r} min" for r in sh.index], text=text,
        texttemplate="%{text}", colorscale="Blues", colorbar=dict(title="Sharpe"),
        hovertemplate="range %{y}, target %{x}<br>Sharpe %{z:.2f}<extra></extra>"))
    bl = verdict["best_loc"]
    fig.add_shape(type="rect", xref="x", yref="y",
                  x0=list(sh.columns).index(bl[1]) - .5, x1=list(sh.columns).index(bl[1]) + .5,
                  y0=RANGES.index(bl[0]) - .5, y1=RANGES.index(bl[0]) + .5,
                  line=dict(color="#eb6834", width=3))
    fig.update_layout(
        template="plotly_white", height=560, margin=dict(l=90, r=30, t=70, b=60),
        title=f"{SYMBOL} opening range breakout: Sharpe by range length and profit target "
              f"(orange box = best; cells under {MIN_TRADES} trades ignored)",
        xaxis=dict(title="Profit target (R = distance from entry to stop)", type="category"),
        yaxis=dict(title="Opening range length", type="category", autorange="reversed"))
    return fig


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    df = run_grid()
    sh = valid_sharpe(df)
    v = judge(sh)

    print(f"{SYMBOL} 5min bars {span}, ${STARTING_EQUITY:,.0f} start, {RISK_PCT:.0%} risk per trade, "
          f"usual costs, long only, no leverage. Cells under {MIN_TRADES} trades ignored.\n")
    print("Sharpe (rows: range minutes, columns: target):")
    print(sh.round(2).to_string(na_rep="  -  "))
    print("\nTrades:")
    print(matrix(df, "trades").astype(int).to_string())
    top = sh.stack().sort_values(ascending=False).head(5)
    print("\nTop 5 cells:", ", ".join(f"{r}min/{t} {s:.2f}" for (r, t), s in top.items()))
    print(f"Best: {v['best_loc'][0]} min, target {v['best_loc'][1]}, Sharpe {v['best']:.2f}")
    print(f"  its {len(v['neighbours'])} neighbours: mean {v['nb_mean']:.2f} ({v['nb_pct_of_best']:.0%} of best), "
          f"worst {v['nb_min']:.2f}")
    print(f"  grid: {v['n_valid']} valid cells, median Sharpe {v['grid_median']:.2f}, "
          f"{v['n_positive']} above zero")
    print(f"  best after smoothing over neighbours: {v['smooth_loc'][0]} min / {v['smooth_loc'][1]} "
          f"({v['smooth_best']:.2f})")

    OUT.write_text(heatmap(df, sh, v).to_html(full_html=True, include_plotlyjs=True), encoding="utf-8")
    print(f"\nheatmap -> {OUT}")


if __name__ == "__main__":
    main()
