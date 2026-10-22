"""Two more stress tests of opening_range_breakout on QQQ 5min (15-minute range, 1R target).

1. Parameter noise: 200 versions of the strategy, each with the range length, the target and the
   last entry time nudged at random around the chosen setting (range 15 min +-7.5 min, rounded to
   a whole 5-min bar; target 1R +-0.3R; last entry 30 min before the close +-20 min). Prints how
   many still make money and how the Sharpe ratio (return per unit of ups and downs) spreads.
2. Regime split: label each month of QQQ rising or falling (close to close change) and calm or
   wild (daily-return volatility above or below the median month), then show how the strategy did
   in each of the four combinations. Months with fewer than 10 sessions are left out.

Prints tables only. Run: uv run -m reports.orb_noise_regimes   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import compute_metrics
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

SYMBOL = "QQQ"
BASE = {"range": 15.0, "target": 1.0, "last_entry": 30.0}  # last entry: minutes before the close
NOISE = {"range": 7.5, "target": 0.3, "last_entry": 20.0}  # uniform +- around the base
N_VERSIONS, SEED, MIN_SESSIONS = 200, 11, 10


def run_version(args: tuple[float, float, float]):
    range_min, target, last_entry = args
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    p = OpeningRangeBreakoutParams(range_minutes=max(5, int(round(range_min / 5)) * 5), target_r=target,
                                   last_entry_minutes_before_close=int(round(last_entry)))
    res = run_backtest(bars, OpeningRangeBreakout(p), costs=CostModel())
    m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
    return {"range": p.range_minutes, "target": target, "last_entry": last_entry,
            "sharpe": float(np.nan_to_num(m["sharpe"])), "net_profit": float(m["net_profit"]),
            "max_dd": float(m["max_drawdown"]), "trades": int(m["trades"])}


def noise_test() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    draws = [tuple(BASE[k] + rng.uniform(-NOISE[k], NOISE[k]) for k in ("range", "target", "last_entry"))
             for _ in range(N_VERSIONS)]
    with ProcessPoolExecutor() as pool:
        return pd.DataFrame(list(pool.map(run_version, draws)))


def print_noise(df: pd.DataFrame, base: dict) -> None:
    prof = df.net_profit > 0
    print(f"\n=== Parameter noise: {len(df)} versions ===")
    print(f"Chosen setting (15 min, 1R, last entry 30 min before close): Sharpe {base['sharpe']:.2f}, "
          f"net ${base['net_profit']:,.0f}, {base['trades']} trades")
    print(f"Versions that make money: {prof.sum()} of {len(df)} ({prof.mean():.0%}); "
          f"Sharpe above 1: {(df.sharpe > 1).mean():.0%}; Sharpe above chosen: {(df.sharpe > base['sharpe']).mean():.0%}")
    q = df[["sharpe", "net_profit", "max_dd"]].quantile([0, .05, .25, .5, .75, .95, 1]).T
    q.columns = ["min", "P5", "P25", "median", "P75", "P95", "max"]
    q.index = ["Sharpe", "Net profit ($)", "Max drawdown"]
    print(q.round(2).to_string())
    df = df.assign(range_bucket=df["range"].astype(int).astype(str) + " min")
    by = df.groupby("range_bucket").agg(versions=("sharpe", "size"), median_sharpe=("sharpe", "median"),
                                        worst_sharpe=("sharpe", "min"), profitable=("net_profit", lambda s: (s > 0).mean()))
    print("\nBy range length:\n" + by.round(2).to_string())
    for col, edges in [("target", [0.7, 0.9, 1.1, 1.3]), ("last_entry", [10, 23, 37, 50])]:
        b = pd.cut(df[col], edges, include_lowest=True)
        g = df.groupby(b, observed=True).agg(versions=("sharpe", "size"), median_sharpe=("sharpe", "median"),
                                             worst_sharpe=("sharpe", "min"), profitable=("net_profit", lambda s: (s > 0).mean()))
        print(f"\nBy {col}{' (R)' if col == 'target' else ' (min before close)'}:\n" + g.round(2).to_string())


def label_months(bars: pd.DataFrame) -> pd.DataFrame:
    local = bars.index.tz_convert("America/New_York")
    day = pd.DataFrame({"open": bars.open.groupby(local.date).first(), "close": bars.close.groupby(local.date).last()})
    day.index = pd.DatetimeIndex(day.index)
    day["ret"] = day.close.pct_change()
    day.loc[day.index[0], "ret"] = day.close.iloc[0] / day.open.iloc[0] - 1
    month = day.index.to_period("M")
    g = pd.DataFrame({"sessions": day.ret.groupby(month).size(),
                      "change": day.close.groupby(month).last() / day.close.groupby(month).first().shift(0) - 1,
                      "vol": day.ret.groupby(month).std()})
    prev_close = day.close.groupby(month).last().shift(1)
    g["change"] = day.close.groupby(month).last() / prev_close.fillna(day.open.iloc[0]) - 1
    g = g[g.sessions >= MIN_SESSIONS].copy()
    g["direction"] = np.where(g.change > 0, "rising", "falling")
    g["wildness"] = np.where(g.vol > g.vol.median(), "wild", "calm")
    g["regime"] = g.direction + " + " + g.wildness
    return g


def regime_test(bars: pd.DataFrame) -> None:
    p = OpeningRangeBreakoutParams(range_minutes=15, target_r=1.0)
    res = run_backtest(bars, OpeningRangeBreakout(p), costs=CostModel())
    months = label_months(bars)
    t = res.trades.copy()
    t["month"] = t.entry_time.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.to_period("M")
    t = t[t.month.isin(months.index)]
    eq = res.equity.copy()
    eq_m = eq.groupby(eq.index.tz_convert("America/New_York").tz_localize(None).to_period("M")).last()
    start = eq_m.shift(1).fillna(STARTING_EQUITY)
    months["account_ret"] = (eq_m / start - 1).reindex(months.index)

    print("\n=== Regime split ===")
    print(f"Sessions per month >= {MIN_SESSIONS}; calm/wild split at the median monthly volatility "
          f"({months.vol.median():.2%} daily std).")
    show = months[["sessions", "change", "vol", "regime", "account_ret"]].copy()
    show["trades"] = t.groupby("month").size().reindex(show.index).fillna(0).astype(int)
    show["net_pnl"] = t.groupby("month").net_pnl.sum().reindex(show.index).fillna(0)
    for c in ("change", "vol", "account_ret"):
        show[c] = (show[c] * 100).round(2).astype(str) + "%"
    show["net_pnl"] = show.net_pnl.round(0).map("${:,.0f}".format)
    print(show.to_string())

    rows = []
    for name in ["rising + calm", "rising + wild", "falling + calm", "falling + wild"]:
        mm = months[months.regime == name]
        tt = t[t.month.isin(mm.index)]
        if tt.empty:
            rows.append({"regime": name, "months": len(mm)})
            continue
        win, loss = tt.net_pnl[tt.net_pnl > 0].sum(), -tt.net_pnl[tt.net_pnl < 0].sum()
        rows.append({"regime": name, "months": len(mm), "trades": len(tt), "net_pnl": tt.net_pnl.sum(),
                     "avg_month_ret": mm.account_ret.mean(), "profitable_months": (mm.account_ret > 0).mean(),
                     "win_rate": (tt.net_pnl > 0).mean(), "avg_R": tt.r_multiple.mean(),
                     "profit_factor": win / loss if loss else np.inf})
    r = pd.DataFrame(rows).set_index("regime")
    print("\nBy regime (trades grouped by the month they opened):")
    fmt = r.copy()
    for c in ("avg_month_ret", "profitable_months", "win_rate"):
        fmt[c] = (fmt[c] * 100).round(1).astype(str) + "%"
    fmt["net_pnl"] = fmt.net_pnl.map("${:,.0f}".format)
    print(fmt.round(2).to_string())
    for label, col in [("direction", "direction"), ("wildness", "wildness")]:
        mm = months.groupby(col).account_ret.agg(["size", "mean"])
        pnl = t.merge(months[[col]], left_on="month", right_index=True).groupby(col).agg(
            trades=("net_pnl", "size"), net_pnl=("net_pnl", "sum"), avg_R=("r_multiple", "mean"))
        print(f"\nBy {label}:\n" + mm.join(pnl).round(4).to_string())


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    print("Chosen setting...", flush=True)
    base = run_version((15.0, 1.0, 30.0))
    print(f"Running {N_VERSIONS} noisy versions...", flush=True)
    print_noise(noise_test(), base)
    regime_test(bars)


if __name__ == "__main__":
    main()
