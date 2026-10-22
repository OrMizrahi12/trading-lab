"""How luck fools us: random strategies, the Deflated Sharpe Ratio and the Probability of Backtest Overfitting.

1) Luck: 1,000 strategies on QQQ that flip a coin every session (long or short, and long or flat),
   enter at the open and exit at the close, no costs. Shows the best one's Sharpe (return per unit of
   day-to-day swing, annualized) next to the median.
2) Deflated Sharpe Ratio (DSR) of opening_range_breakout on QQQ 5min, 15 minute range, 1R target: the
   probability that the true Sharpe is above zero after allowing for how many settings were tried
   (the 42 grid cells, 42 random search and 42 Optuna trials, 200 noise versions = 326 full-data runs),
   the short data and the shape of the returns. Also shown with other trial counts.
3) Probability of Backtest Overfitting (PBO) of the 42-cell grid from backtests.optimize, by
   combinatorially symmetric cross-validation (CSCV): the daily returns are cut into S time blocks; for
   every way of using half the blocks as training, the best training cell is ranked on the other half.
   PBO is the share of splits where that winner lands in the bottom half out of sample.

Only numpy and pandas; the normal distribution comes from the standard library.
Run: uv run -m reports.luck_vs_skill   (about 8 minutes: it reruns the grid, the random search, the Optuna
search and the noise test)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import itertools
from concurrent.futures import ProcessPoolExecutor
from math import e, sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import TRADING_DAYS, compute_metrics, daily_returns
from backtests.optimize import RANGES, SYMBOL, TARGETS, target_label
from backtests.search_comparison import optuna_search, random_search
from backtests.vectorized import CACHE, load_bars
from reports.orb_noise_regimes import noise_test
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

N_RANDOM, RANDOM_SEED = 1000, 2026
CHOSEN = (15, "1R")  # range minutes, target label
MIN_TRADES = 100
WALK_FORWARD_AND_SPLIT_RUNS = 540 + 42  # tuning runs in walk_forward (9 windows x 12 trials x 5 seeds) and split_validation
EULER = 0.5772156649015329
ANN = sqrt(TRADING_DAYS)
NORM = NormalDist()


def grid_cell(args: tuple[int, float | None]) -> dict:
    minutes, target = args
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    params = OpeningRangeBreakoutParams(range_minutes=minutes, target_r=target)
    res = run_backtest(bars, OpeningRangeBreakout(params), costs=CostModel())
    m = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
    return {"range": minutes, "target": target_label(target), "trades": int(m["trades"]),
            "sharpe": float(np.nan_to_num(m["sharpe"])), "daily": daily_returns(res.equity, STARTING_EQUITY)}


def collect() -> dict:
    cells = [(r, t) for r in RANGES for t in TARGETS]
    with ProcessPoolExecutor() as pool:
        grid = list(pool.map(grid_cell, cells))
    return {"grid": grid, "random": random_search(), "optuna": optuna_search(), "noise": noise_test()}


def luck(bars: pd.DataFrame) -> None:
    day = bars.index.tz_convert("America/New_York").date
    g = bars.groupby(day)
    oc = (g["close"].last() / g["open"].first() - 1).to_numpy()  # open-to-close return per session
    rng = np.random.default_rng(RANDOM_SEED)
    coin = rng.integers(0, 2, size=(N_RANDOM, len(oc)))

    def sharpe(r: np.ndarray) -> np.ndarray:
        return r.mean(axis=1) / r.std(axis=1, ddof=1) * ANN

    hold = oc.mean() / oc.std(ddof=1) * ANN
    print(f"=== 1. LUCK: {N_RANDOM} random strategies, QQQ open->close, {len(oc)} sessions "
          f"({bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}), no costs ===")
    for name, s in (("long or short by coin flip", sharpe(np.where(coin == 1, 1.0, -1.0) * oc)),
                    ("long or flat by coin flip", sharpe(coin * oc))):
        print(f"{name:28s} best {s.max():5.2f} | 95th pct {np.percentile(s, 95):5.2f} | median {np.median(s):5.2f} | "
              f"worst {s.min():5.2f} | above 1.0: {(s > 1).sum()} | above 0: {(s > 0).mean():.0%}")
    print(f"(buy QQQ every open, sell every close: Sharpe {hold:.2f})")


def deflated_sharpe(d: dict) -> None:
    grid = d["grid"]
    chosen = next(c for c in grid if (c["range"], c["target"]) == CHOSEN)
    r = chosen["daily"].to_numpy()
    n_days = len(r)
    sr = r.mean() / r.std(ddof=1)  # per-day Sharpe
    z = (r - r.mean()) / r.std()  # population moments, as in the DSR paper
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())  # kurtosis counts the normal's 3

    trial_sr = np.concatenate([[c["sharpe"] for c in grid], d["random"]["sharpe"].to_numpy(),
                               d["optuna"]["sharpe"].to_numpy(), d["noise"]["sharpe"].to_numpy()]) / ANN
    n_full = len(trial_sr)
    var_sr = trial_sr.var(ddof=1)

    def bar_to_beat(n: int) -> float:
        """Sharpe the luckiest of n unskilled trials would reach (expected maximum)."""
        return sqrt(var_sr) * ((1 - EULER) * NORM.inv_cdf(1 - 1 / n) + EULER * NORM.inv_cdf(1 - 1 / (n * e)))

    def dsr(star: float) -> float:
        denom = sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
        return NORM.cdf((sr - star) * sqrt(n_days - 1) / denom)

    n_grid = len(grid)
    print(f"\n=== 2. DEFLATED SHARPE: ORB QQQ, {CHOSEN[0]} min range, {CHOSEN[1]} target ===")
    print(f"trials with their Sharpe: grid {n_grid}, random search {len(d['random'])}, Optuna {len(d['optuna'])}, "
          f"noise test {len(d['noise'])} = {n_full} settings on the full data")
    print(f"chosen: Sharpe {sr * ANN:.2f} annual ({sr:.4f}/day), {n_days} days, {chosen['trades']} trades, "
          f"skew {skew:.2f}, kurtosis {kurt:.2f}")
    print(f"spread of Sharpe across all {n_full} trials: std {sqrt(var_sr) * ANN:.2f} annual "
          f"(mean {trial_sr.mean() * ANN:.2f}, max {trial_sr.max() * ANN:.2f})")
    print(f"{'trials N':>10s} {'bar to beat SR0':>16s} {'DSR':>7s}")
    corr = np.corrcoef(np.vstack([c["daily"].to_numpy() for c in grid]))
    rho = corr[np.triu_indices(n_grid, 1)].mean()
    n_eff = rho + (1 - rho) * n_grid  # effective number of independent grid trials
    rows = [("1 (no deflation)", 0.0), (f"{n_grid} grid only", bar_to_beat(n_grid)),
            (f"{n_full} full-data runs", bar_to_beat(n_full)),
            (f"{n_full + WALK_FORWARD_AND_SPLIT_RUNS} + walk-fwd/split", bar_to_beat(n_full + WALK_FORWARD_AND_SPLIT_RUNS))]
    for lab, star in rows:
        print(f"{lab:>24s} {star * ANN:10.2f} annual {dsr(star):7.1%}")
    print(f"average correlation between the {n_grid} grid cells' daily returns: {rho:.2f} "
          f"-> effective independent grid trials about {n_eff:.0f}")
    star = bar_to_beat(max(2, round(n_eff)))
    print(f"{'effective ' + str(round(n_eff)):>24s} {star * ANN:10.2f} annual {dsr(star):7.1%}")
    h = n_days // 2
    print(f"chosen Sharpe first half {r[:h].mean() / r[:h].std(ddof=1) * ANN:.2f}, "
          f"second half {r[h:].mean() / r[h:].std(ddof=1) * ANN:.2f}")


def cscv(m: np.ndarray, s: int) -> dict:
    """Combinatorially symmetric cross-validation of the columns (strategies) of the daily return matrix m."""
    n_cols = m.shape[1]
    days = (m.shape[0] // s) * s
    m = m[-days:]  # drop the oldest few days so the blocks are equal
    size = days // s
    blocks = m.reshape(s, size, -1)
    total, total_sq = blocks.sum(axis=1), (blocks ** 2).sum(axis=1)  # per block and strategy

    def sharpe(idx: list[int]) -> np.ndarray:
        cnt = len(idx) * size
        mean = total[idx].sum(0) / cnt
        var = (total_sq[idx].sum(0) - cnt * mean ** 2) / (cnt - 1)
        return np.where(var > 1e-18, mean / np.sqrt(np.maximum(var, 1e-18)), 0.0)

    logits, in_sample, out_sample = [], [], []
    for train in itertools.combinations(range(s), s // 2):
        a, b = sharpe(list(train)), sharpe([i for i in range(s) if i not in train])
        j = int(np.argmax(a))
        rank = (b < b[j]).sum() + ((b == b[j]).sum() + 1) / 2  # 1 = worst, N = best; ties share the middle rank
        w = rank / (n_cols + 1)
        logits.append(np.log(w / (1 - w)))
        in_sample.append(a[j]), out_sample.append(b[j])
    logits, ins, oos = np.array(logits), np.array(in_sample) * ANN, np.array(out_sample) * ANN
    return {"pbo": float((logits <= 0).mean()), "n": len(logits), "days": days, "is": ins, "oos": oos,
            "loss": float((oos < 0).mean()), "slope": float(np.polyfit(ins, oos, 1)[0])}


def overfitting(d: dict) -> None:
    grid = d["grid"]
    m_all = np.column_stack([c["daily"].to_numpy() for c in grid])
    valid = np.array([c["trades"] >= MIN_TRADES for c in grid])
    print(f"\n=== 3. PBO by CSCV on the grid ===")
    for name, m in (("all 42 cells", m_all), (f"{valid.sum()} cells with >={MIN_TRADES} trades", m_all[:, valid])):
        for s in (8, 16):
            res = cscv(m, s)
            print(f"{name:28s} S={s:2d} ({res['n']:5d} splits, {res['days']} days): PBO {res['pbo']:.0%} | "
                  f"IS-winner Sharpe in-sample {res['is'].mean():.2f} -> out-of-sample {res['oos'].mean():.2f} "
                  f"(median {np.median(res['oos']):.2f}) | OOS Sharpe<0 in {res['loss']:.0%} | slope {res['slope']:.2f}")


def main() -> None:
    luck(load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet")))
    d = collect()
    deflated_sharpe(d)
    overfitting(d)


if __name__ == "__main__":
    main()
