"""Honest out-of-sample test of the opening range breakout grid on QQQ 5min.

The 24 months of data are cut into three consecutive parts: the first 12 months for tuning, the
next 6 for validation, the last 6 as a holdout. The grid (range length x profit target, same as
backtests.optimize) runs on the tuning part only. Two candidates come out of it: the single best
cell, and the best cell after smoothing (each cell averaged with its neighbours, so a lone spike
does not win). Both are then run on validation, and finally each once on the holdout. The holdout
is not loaded into any grid run; it is only touched in the last step, for the two chosen cells.
Cells need at least 50 trades (half of optimize.py's 100, because the tuning part is half the data).
Run: uv run -m backtests.split_validation   (about a minute)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import compute_metrics
from backtests.optimize import RANGES, SYMBOL, TARGETS, judge, matrix, target_label
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

MIN_TRADES = 50


def split_bars(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Tuning = first 12 months, validation = next 6, holdout = the rest (cut on session dates)."""
    start = bars.index[0].tz_convert("America/New_York").normalize().tz_localize(None)
    cut1, cut2 = start + pd.DateOffset(months=12), start + pd.DateOffset(months=18)
    day = bars.index.tz_convert("America/New_York").tz_localize(None).normalize()
    return {"tuning": bars[day < cut1], "validation": bars[(day >= cut1) & (day < cut2)],
            "holdout": bars[day >= cut2]}


def run_cell(args: tuple) -> dict:
    bars, minutes, target = args
    params = OpeningRangeBreakoutParams(range_minutes=minutes, target_r=target)
    res = run_backtest(bars, OpeningRangeBreakout(params), costs=CostModel())
    m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
    return {"range": minutes, "target": target_label(target), **m}


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    parts = split_bars(bars)
    for name, p in parts.items():
        print(f"{name:10s} {p.index[0]:%Y-%m-%d} to {p.index[-1]:%Y-%m-%d}  {len(p)} bars")

    # 1) grid on the tuning part only
    cells = [(parts["tuning"], r, t) for r in RANGES for t in TARGETS]
    with ProcessPoolExecutor() as pool:
        df = pd.DataFrame(list(pool.map(run_cell, cells)))
    sh = matrix(df, "sharpe").where(matrix(df, "trades") >= MIN_TRADES)
    v = judge(sh)
    print("\nTuning Sharpe (rows: range minutes, columns: target):")
    print(sh.round(2).to_string(na_rep="  -  "))
    print("\nTuning trades:")
    print(matrix(df, "trades").astype(int).to_string())

    tgt = {target_label(t): t for t in TARGETS}
    cands = {"best cell": v["best_loc"], "smoothed plateau": v["smooth_loc"]}
    print(f"\nCandidates: best cell {cands['best cell']} (Sharpe {v['best']:.2f}, neighbours' mean "
          f"{v['nb_mean']:.2f}); smoothed plateau {cands['smoothed plateau']} (smoothed {v['smooth_best']:.2f})")

    # 2) validation, 3) holdout: the two candidates only, each run once per part
    rows = []
    for name, (rng, tl) in cands.items():
        row = {"candidate": name, "cell": f"{rng}min/{tl}"}
        for part in ("tuning", "validation", "holdout"):
            m = run_cell((parts[part], rng, tgt[tl]))
            row[f"{part} sharpe"], row[f"{part} trades"] = m["sharpe"], m["trades"]
            row[f"{part} net %"] = 100 * m["net_profit"] / STARTING_EQUITY
            row[f"{part} maxDD"] = m["max_drawdown"]
        rows.append(row)
    out = pd.DataFrame(rows).set_index("candidate")
    pd.set_option("display.width", 250)
    print("\n", out.round(2).T.to_string())


if __name__ == "__main__":
    main()
