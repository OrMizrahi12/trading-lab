"""Random search vs. Optuna vs. the grid, on opening_range_breakout on QQQ 5min.

Both searches get 42 trials (the grid had 42 cells) over continuous ranges: opening range length
5-60 minutes, profit target 0.5-10 R, last entry time 10:00-15:00. Same objective as the grid
(Sharpe from the shared engine, usual costs); a trial with fewer than 100 trades does not count.
Random search draws every trial blindly; Optuna's TPE sampler learns from earlier trials where
the good settings sit and samples there more often. The range length is rounded to a whole 5-min
bar (the strategy needs that) and the last entry to a whole minute (bars are 5 min, so in effect
5 min). Last entry is given to the strategy as minutes before the close (10:00 = 360, 15:00 = 60),
so a half day moves with its early close.

The last section shows why the best number is too optimistic: how far the best trial sits above the
typical trial, and how each winner does on the first half vs. the second half of the data.
Run: uv run -m backtests.search_comparison   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import optuna
import pandas as pd

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import compute_metrics
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

SYMBOL = "QQQ"
N_TRIALS = 42
MIN_TRADES = 100
SEED = 7
GRID_BEST = 1.77  # grid winner: 15 min range, 1R target
CLOSE_FRAC = 0.90  # "close" = Sharpe within 10% of the grid best
LO_HI = {"range": (5, 60), "target": (0.5, 10.0), "last_entry": (10 * 60, 15 * 60)}  # last entry in minutes after midnight


def to_params(range_min: float, target: float, last_entry: float) -> OpeningRangeBreakoutParams:
    return OpeningRangeBreakoutParams(
        range_minutes=int(round(range_min / 5)) * 5, target_r=float(target),
        last_entry_minutes_before_close=int(round(16 * 60 - last_entry)))


def evaluate(args: tuple[float, float, float], bars: pd.DataFrame | None = None) -> dict:
    range_min, target, last_entry = args
    if bars is None:
        bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    res = run_backtest(bars, OpeningRangeBreakout(to_params(*args)), costs=CostModel())
    if len(res.trades) == 0:
        return {"range": range_min, "target": target, "last_entry": last_entry, "sharpe": 0.0, "trades": 0}
    m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
    return {"range": range_min, "target": target, "last_entry": last_entry,
            "sharpe": float(np.nan_to_num(m["sharpe"])), "trades": int(m["trades"])}


def random_search() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    draws = [tuple(rng.uniform(*LO_HI[k]) for k in ("range", "target", "last_entry")) for _ in range(N_TRIALS)]
    with ProcessPoolExecutor() as pool:
        return pd.DataFrame(list(pool.map(evaluate, draws)))


def optuna_search() -> pd.DataFrame:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        a = tuple(trial.suggest_float(k, *LO_HI[k]) for k in ("range", "target", "last_entry"))
        r = evaluate(a, bars)
        trial.set_user_attr("trades", r["trades"])
        return r["sharpe"]

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(
            seed=SEED, constraints_func=lambda t: (MIN_TRADES - t.user_attrs["trades"],)))  # <= 0 is allowed
    study.optimize(objective, n_trials=N_TRIALS)
    return pd.DataFrame([{**t.params, "sharpe": t.value, "trades": t.user_attrs["trades"]} for t in study.trials])


def valid(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["trades"] >= MIN_TRADES]


def best_so_far(df: pd.DataFrame) -> pd.Series:
    s = df["sharpe"].where(df["trades"] >= MIN_TRADES)
    return s.cummax()


def trials_to_close(df: pd.DataFrame) -> str:
    hit = best_so_far(df) >= CLOSE_FRAC * GRID_BEST
    return str(int(hit.idxmax()) + 1) if hit.any() else f"never in {len(df)}"


def clock(minutes: float) -> str:
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}"


def describe(row: pd.Series) -> str:
    p = to_params(row["range"], row["target"], row["last_entry"])
    return (f"{p.range_minutes} min, {row['target']:.2f}R, last entry {clock(16 * 60 - p.last_entry_minutes_before_close)}")


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    rnd, opt = random_search(), optuna_search()
    print(f"{SYMBOL} ORB, {N_TRIALS} trials per method, Sharpe objective, >= {MIN_TRADES} trades, usual costs.\n")

    rows = []
    for name, df in (("Random search", rnd), ("Optuna (TPE)", opt)):
        v = valid(df)
        b = v.loc[v["sharpe"].idxmax()]
        rows.append((name, describe(b), b["sharpe"], int(b["trades"]), len(v), v["sharpe"].median(),
                     trials_to_close(df), int(best_so_far(df).idxmax()) + 1))
    print(f"{'method':<15}{'best settings':<38}{'Sharpe':>7}{'trades':>8}{'valid':>7}{'median':>8}"
          f"{'to 90% of grid':>16}{'best at trial':>15}")
    print(f"{'Grid (42 cells)':<15}{'15 min, 1.00R, last entry 15:30':<38}{GRID_BEST:>7.2f}{'':>8}{'':>7}{'':>8}"
          f"{'(42 cells)':>16}{'':>15}")
    for r in rows:
        print(f"{r[0]:<15}{r[1]:<38}{r[2]:>7.2f}{r[3]:>8}{r[4]:>7}{r[5]:>8.2f}{r[6]:>16}{r[7]:>15}")

    print(f"\nBest-so-far Sharpe after N trials (grid best {GRID_BEST}):")
    marks = [5, 10, 20, 30, 42]
    print(f"{'trials':<10}" + "".join(f"{m:>8}" for m in marks))
    for name, df in (("random", rnd), ("optuna", opt)):
        bs = best_so_far(df)
        print(f"{name:<10}" + "".join(f"{bs.iloc[m - 1]:>8.2f}" for m in marks))

    print("\nWhy the best number is too optimistic: same winners on each half of the data")
    mid = bars.index[len(bars) // 2]
    first, second = bars[bars.index < mid], bars[bars.index >= mid]
    print(f"  first half {first.index[0]:%Y-%m-%d} to {first.index[-1]:%Y-%m-%d}, "
          f"second half {second.index[0]:%Y-%m-%d} to {second.index[-1]:%Y-%m-%d}")
    print(f"  {'settings':<42}{'full':>7}{'1st half':>10}{'2nd half':>10}")
    for name, df in (("grid", None), ("random", rnd), ("optuna", opt)):
        if df is None:
            a = (15, 1.0, 15 * 60 + 30)
        else:
            v = valid(df)
            b = v.loc[v["sharpe"].idxmax()]
            a = (b["range"], b["target"], b["last_entry"])
        full, h1, h2 = evaluate(a, bars)["sharpe"], evaluate(a, first)["sharpe"], evaluate(a, second)["sharpe"]
        label = f"{name}: " + describe(pd.Series({"range": a[0], "target": a[1], "last_entry": a[2]}))
        print(f"  {label:<42}{full:>7.2f}{h1:>10.2f}{h2:>10.2f}")
    for name, df in (("random", rnd), ("optuna", opt)):
        v = valid(df)["sharpe"]
        print(f"  {name}: best {v.max():.2f} vs. median {v.median():.2f}, "
              f"{(v > 0).sum()} of {len(v)} valid trials above zero, spread (std) {v.std():.2f}")


if __name__ == "__main__":
    main()
