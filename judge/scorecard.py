"""The judge: runs the lab's whole battery on one strategy and prints PASS or FAIL per rule.

    uv run -m judge.scorecard opening_range_breakout --symbol QQQ --set range_minutes=15 --set target_r=1.0
    uv run -m judge.scorecard donchian_breakout --symbol SPY
    uv run -m judge.scorecard bollinger_rsi_reversion --symbol QQQ --trials 250

Eight rules, thresholds in judge/standards.yaml (with the reasoning for each):
1. trade count, 2. Sharpe after costs, 3. walk-forward out-of-sample Sharpe (re-tuned each window),
4. the fixed settings' Sharpe on the same months, 5. Monte Carlo drawdown at the 5th percentile,
6. break-even cost multiple, 7. Deflated Sharpe Ratio, 8. parameter noise.
The tests are the ones from backtests/walk_forward.py, reports/orb_stress_test.py, reports/orb_noise_regimes.py
and reports/luck_vs_skill.py, made strategy-agnostic and cut down so one run takes a couple of minutes:
every backtest (main run, cost sweep, noise versions, walk-forward tuning) goes into one process pool.

From code: `scorecard(name, symbol, settings, trials=...)` returns {"passed": bool, "rules": [...], ...}.
The CLI exits 1 when any rule fails. `trials` is how many settings the research tried before these;
the Deflated Sharpe needs it, so a research loop should pass the real count.

To judge a new strategy add it to STRATEGIES with the settings Optuna may tune and their ranges.

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import argparse
import ast
import os
import re
import sys
import time as clock
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import time
from math import e, sqrt
from pathlib import Path
from statistics import NormalDist

import numpy as np
import optuna
import pandas as pd
import yaml

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import TRADING_DAYS, compute_metrics, daily_returns
from backtests.vectorized import CACHE, load_bars
from backtests.walk_forward import cut, make_windows
from reports.orb_stress_test import monte_carlo, pct, scaled, trade_returns
from strategies.bollinger_rsi_reversion import BollingerRsiReversion, BollingerRsiReversionParams
from strategies.donchian_breakout import DonchianBreakout, DonchianBreakoutParams
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams
from strategies.vwap_stretch_reversion import VwapStretchReversion, VwapStretchReversionParams

STANDARDS = Path(__file__).with_name("standards.yaml")
ANN = sqrt(TRADING_DAYS)
EULER = 0.5772156649015329
NORM = NormalDist()


@dataclass(frozen=True)
class Spec:
    strategy: type
    params: type
    space: dict[str, tuple]  # setting -> (low, high, step); step None = continuous. What walk-forward tunes and noise nudges.


STRATEGIES = {
    "opening_range_breakout": Spec(OpeningRangeBreakout, OpeningRangeBreakoutParams,
                                   {"range_minutes": (5, 60, 5), "target_r": (0.5, 10.0, None),
                                    "max_hold_bars": (3, 36, 1)}),
    "donchian_breakout": Spec(DonchianBreakout, DonchianBreakoutParams,
                              {"channel_bars": (10, 60, 5), "atr_multiple": (1.0, 4.0, None)}),
    "bollinger_rsi_reversion": Spec(BollingerRsiReversion, BollingerRsiReversionParams,
                                    {"bb_period": (10, 40, 5), "bb_k": (1.5, 3.0, None),
                                     "rsi_max": (5.0, 25.0, None), "atr_multiple": (1.0, 4.0, None)}),
    "vwap_stretch_reversion": Spec(VwapStretchReversion, VwapStretchReversionParams,
                                   {"stretch_atr_multiple": (1.0, 4.0, None), "atr_multiple": (1.0, 4.0, None)}),
}


@dataclass
class Rule:
    name: str
    value: str
    needs: str
    passed: bool
    note: str = ""


def load_standards(path: Path = STANDARDS) -> dict:
    return yaml.safe_load(path.read_text())


def bars_for(symbol: str) -> pd.DataFrame:
    return load_bars(CACHE.with_name(f"{symbol}_5min.parquet"))


def build(name: str, settings: dict):
    spec = STRATEGIES[name]
    return spec.strategy(spec.params(**settings))


def sharpe_of(res) -> float:
    if res.trades.empty:
        return 0.0
    return float(np.nan_to_num(compute_metrics(res.trades, res.equity, STARTING_EQUITY)["sharpe"]))


# ---- workers (top level so the process pool can pickle them) ----

def job_run(name: str, symbol: str, settings: dict, k: float):
    """One full-data backtest with costs at k times the usual."""
    return run_backtest(bars_for(symbol), build(name, settings), costs=scaled(k))


def job_noise(name: str, symbol: str, settings: dict) -> dict:
    res = run_backtest(bars_for(symbol), build(name, settings), costs=CostModel())
    return {"sharpe": sharpe_of(res), "net_profit": float(res.trades.net_pnl.sum()) if len(res.trades) else 0.0}


def job_tune(name: str, symbol: str, settings: dict, span: tuple, wf: dict) -> dict:
    """Optuna on one tuning window. Falls back to the given settings if no trial had enough trades."""
    warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)  # constraints_func, same as walk_forward.py
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    bars = cut(bars_for(symbol), span)
    space = STRATEGIES[name].space

    def trial_settings(trial: optuna.Trial) -> dict:
        s = dict(settings)
        for key, (lo, hi, step) in space.items():
            s[key] = trial.suggest_int(key, lo, hi, step=step) if step is not None \
                else trial.suggest_float(key, lo, hi)
        return s

    def objective(trial: optuna.Trial) -> float:
        res = run_backtest(bars, build(name, trial_settings(trial)), costs=CostModel())
        trial.set_user_attr("trades", len(res.trades))
        return sharpe_of(res)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(
        seed=wf["seed"], n_startup_trials=wf["optuna_startup"],
        constraints_func=lambda t: (wf["min_trades_per_tune"] - t.user_attrs["trades"],)))  # <= 0 is allowed
    study.optimize(objective, n_trials=wf["optuna_trials"])
    ok = [t for t in study.trials if t.user_attrs["trades"] >= wf["min_trades_per_tune"]]
    if not ok:
        return {"settings": dict(settings), "tune_sharpe": float("nan"), "fallback": True}
    best = max(ok, key=lambda t: t.value)
    return {"settings": {**settings, **best.params}, "tune_sharpe": best.value, "fallback": False}


# ---- the tests ----

def noise_versions(name: str, settings: dict, cfg: dict) -> list[dict]:
    """`versions` copies of the settings, each tunable setting nudged by up to +-relative, last entry by +-minutes."""
    spec = STRATEGIES[name]
    full = {f: getattr(spec.params(), f) for f in spec.params.__dataclass_fields__} | settings
    rng = np.random.default_rng(cfg["seed"])
    out = []
    for _ in range(cfg["versions"]):
        s = dict(settings)
        for key, (lo, hi, step) in spec.space.items():
            v = full[key]
            if v is None:  # e.g. an ORB with no profit target stays without one
                continue
            new = v * (1 + rng.uniform(-cfg["relative"], cfg["relative"]))
            s[key] = max(step, int(round(new / step)) * step) if isinstance(step, int) else new
        last = full["last_entry_minutes_before_close"]
        s["last_entry_minutes_before_close"] = max(full["flatten_minutes_before_close"] + 5,
                                                   int(round(last + rng.uniform(-cfg["last_entry_minutes"], cfg["last_entry_minutes"]))))
        out.append(s)
    return out


def break_even(sweep: dict[float, float]) -> float | None:
    """Cost multiple where net profit crosses zero, interpolated between the swept multiples; None = never in range."""
    ks = sorted(sweep)
    for lo, hi in zip(ks, ks[1:]):
        if sweep[hi] <= 0:
            if sweep[lo] <= 0:
                return float(lo)
            return lo + (hi - lo) * sweep[lo] / (sweep[lo] - sweep[hi])
    return None


def deflated_sharpe(daily: np.ndarray, trial_sharpes: np.ndarray, n_trials: int) -> tuple[float, float]:
    """(DSR, bar to beat as a yearly Sharpe). `trial_sharpes` are yearly Sharpes of comparable settings."""
    n = len(daily)
    if n < 3 or daily.std() == 0:
        return 0.0, float("nan")
    sr = daily.mean() / daily.std(ddof=1)
    z = (daily - daily.mean()) / daily.std()
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    var = float(np.var(trial_sharpes / ANN, ddof=1)) if len(trial_sharpes) > 1 else 0.0
    n_trials = max(int(n_trials), 1)
    star = 0.0 if n_trials < 2 else sqrt(var) * ((1 - EULER) * NORM.inv_cdf(1 - 1 / n_trials)
                                                 + EULER * NORM.inv_cdf(1 - 1 / (n_trials * e)))
    denom = sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12))
    return NORM.cdf((sr - star) * sqrt(n - 1) / denom), star * ANN


def walk_forward(name: str, symbol: str, settings: dict, windows: list[dict], picks: list[dict]) -> dict:
    """Trade each window with its pick, chaining the account value; returns the stitched out-of-sample result."""
    bars, eq, parts = bars_for(symbol), STARTING_EQUITY, []
    for w, pick in zip(windows, picks):
        res = run_backtest(cut(bars, w["trade"]), build(name, pick["settings"]), equity=eq, costs=CostModel())
        eq = float(res.equity.iloc[-1])
        parts.append(res)
    trades = pd.concat([p.trades for p in parts], ignore_index=True)
    equity = pd.concat([p.equity for p in parts])
    m = compute_metrics(trades, equity, STARTING_EQUITY)
    won = sum(float(p.equity.iloc[-1] / p.equity.iloc[0]) > 1 for p in parts)
    return {"sharpe": float(np.nan_to_num(m["sharpe"])), "total_return": float(equity.iloc[-1] / STARTING_EQUITY - 1),
            "trades": int(m["trades"]), "windows": len(parts), "windows_won": won,
            "tune_sharpe": float(np.mean(tuned)) if (tuned := [p["tune_sharpe"] for p in picks if not np.isnan(p["tune_sharpe"])]) else float("nan"),
            "fallbacks": sum(p["fallback"] for p in picks)}


def scorecard(name: str, symbol: str, settings: dict, standards: dict | None = None, trials: int | None = None,
              log=lambda s: print(s, file=sys.stderr, flush=True)) -> dict:
    t0 = clock.time()
    std = standards or load_standards()
    rules_cfg, tests = std["rules"], std["tests"]
    n_trials = trials if trials is not None else tests["assumed_trials"]
    bars = bars_for(symbol)
    wf, mc, sweep_cfg, noise_cfg = tests["walk_forward"], tests["monte_carlo"], tests["cost_sweep"], tests["noise"]
    windows = make_windows(bars)
    versions = noise_versions(name, settings, noise_cfg)
    mults = [k for k in sweep_cfg["multiples"] if k != 1]

    log(f"running {len(windows)} tuning windows, {len(versions)} noise versions and {len(mults) + 1} cost runs "
        f"on {os.cpu_count()} cores...")
    with ProcessPoolExecutor() as pool:
        fixed = [{"settings": dict(settings), "tune_sharpe": float("nan"), "fallback": False}] * len(windows)
        fixed_f = pool.submit(walk_forward, name, symbol, settings, windows, fixed)  # same months, no re-tuning
        tuning = [pool.submit(job_tune, name, symbol, settings, w["tune"], wf) for w in windows]  # longest chains first
        base_f = pool.submit(job_run, name, symbol, settings, 1.0)
        sweep_f = {k: pool.submit(job_run, name, symbol, settings, k) for k in mults}
        noise_f = [pool.submit(job_noise, name, symbol, v) for v in versions]
        base = base_f.result()
        picks = [f.result() for f in tuning]
        log(f"tuning done after {clock.time() - t0:.0f} s; trading the out-of-sample months...")
        oos = walk_forward(name, symbol, settings, windows, picks)
        oos_fixed = fixed_f.result()
        sweep = {1.0: float(base.trades.net_pnl.sum()) if len(base.trades) else 0.0}
        sweep |= {k: (float(f.result().trades.net_pnl.sum()) if len(f.result().trades) else 0.0) for k, f in sweep_f.items()}
        noise = pd.DataFrame([f.result() for f in noise_f])

    n_trades = len(base.trades)
    m = compute_metrics(base.trades, base.equity, STARTING_EQUITY)
    sharpe = float(np.nan_to_num(m["sharpe"]))
    daily = daily_returns(base.equity, STARTING_EQUITY).to_numpy()

    if n_trades >= 2:
        rets = trade_returns(base)
        sim = monte_carlo(rets)
    dd_p5 = pct(sim["dd"], mc["percentile"]) if n_trades >= 2 else float("nan")
    be = break_even(sweep)
    dsr, star = deflated_sharpe(daily, np.append(noise.sharpe.to_numpy(), sharpe), n_trials)
    share = float((noise.net_profit > 0).mean())

    be_text = f"above {max(sweep)}x" if be is None else f"{be:.1f}x"
    rules = [
        Rule("Trades", f"{n_trades}", f">= {rules_cfg['min_trades']}", n_trades >= rules_cfg["min_trades"]),
        Rule("Sharpe after costs", f"{sharpe:.2f}", f">= {rules_cfg['min_sharpe']}", sharpe >= rules_cfg["min_sharpe"],
             f"net ${m['net_profit']:,.0f}, max drawdown {m['max_drawdown']:.1%}"),
        Rule("Walk-forward Sharpe", f"{oos['sharpe']:.2f}", f">= {rules_cfg['min_walk_forward_sharpe']}",
             oos["sharpe"] >= rules_cfg["min_walk_forward_sharpe"],
             f"{oos['windows_won']} of {oos['windows']} traded windows made money, tuning Sharpe averaged "
             f"{oos['tune_sharpe']:.2f}" + (f", {oos['fallbacks']} window(s) found no valid tuning" if oos["fallbacks"] else "")),
        Rule("Fixed-settings walk-forward", f"{oos_fixed['sharpe']:.2f}", f">= {rules_cfg['min_fixed_settings_sharpe']}",
             oos_fixed["sharpe"] >= rules_cfg["min_fixed_settings_sharpe"],
             f"{oos_fixed['windows_won']} of {oos_fixed['windows']} traded windows made money, no re-tuning"),
        Rule("Monte Carlo drawdown, P5", f"{dd_p5:.1%}", f">= {rules_cfg['max_drawdown_p5']:.0%}",
             dd_p5 >= rules_cfg["max_drawdown_p5"], f"real max drawdown {m['max_drawdown']:.1%}"),
        Rule("Break-even cost multiple", be_text, f">= {rules_cfg['min_break_even_multiple']}x",
             be is None or be >= rules_cfg["min_break_even_multiple"],
             "net profit at 0/1/" + "/".join(f"{k:g}" for k in mults if k > 1) + "x costs: "
             + ", ".join(f"${sweep[k]:,.0f}" for k in sorted(sweep))),
        Rule("Deflated Sharpe", f"{dsr:.1%}", f">= {rules_cfg['min_deflated_sharpe']:.0%}",
             dsr >= rules_cfg["min_deflated_sharpe"],
             f"after {n_trials} trials{' (assumed)' if trials is None else ''}, bar to beat {star:.2f}"),
        Rule("Parameter noise", f"{share:.0%}", f">= {rules_cfg['min_noise_profitable_share']:.0%}",
             share >= rules_cfg["min_noise_profitable_share"],
             f"{int((noise.net_profit > 0).sum())} of {len(noise)} nudged versions profitable, "
             f"Sharpe P5 {noise.sharpe.quantile(.05):.2f}, median {noise.sharpe.median():.2f}"),
    ]
    if n_trades < 2:  # nothing to test; the other numbers above are placeholders
        for r in rules[1:]:
            r.passed = False
    return {"strategy": name, "symbol": symbol, "settings": settings, "rules": rules, "passed": all(r.passed for r in rules),
            "span": (bars.index[0], bars.index[-1]), "seconds": clock.time() - t0, "walk_forward_picks": picks}


def render(card: dict) -> str:
    rules = card["rules"]
    a, b = card["span"]
    lines = [f"SCORECARD  {card['strategy']} on {card['symbol']} 5min  {card['settings'] or '(default settings)'}",
             f"data {a:%Y-%m-%d} to {b:%Y-%m-%d}, usual costs, 1% risk per trade; took {card['seconds']:.0f} s", "",
             f"{'#':<3}{'rule':<28}{'result':>12}{'needs':>10}   verdict"]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i:<3}{r.name:<28}{r.value:>12}{r.needs:>10}   {'PASS' if r.passed else 'FAIL'}")
    lines.append("")
    for i, r in enumerate(rules, 1):
        if r.note:
            lines.append(f"  {i}. {r.note}")
    n = sum(r.passed for r in rules)
    lines += ["", f"OVERALL: {'PASS' if card['passed'] else 'FAIL'} ({n} of {len(rules)} rules passed)"]
    return "\n".join(lines)


def parse_value(raw: str):
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        h, mi = raw.split(":")
        return time(int(h), int(mi))
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("strategy", choices=sorted(STRATEGIES))
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="a strategy setting; repeat for several")
    ap.add_argument("--trials", type=int, help="how many settings the research tried (default: assumed_trials in standards.yaml)")
    ap.add_argument("--standards", type=Path, default=STANDARDS)
    args = ap.parse_args()
    settings = {k: parse_value(v) for k, v in (s.split("=", 1) for s in args.set)}
    card = scorecard(args.strategy, args.symbol, settings, load_standards(args.standards), args.trials)
    print(render(card))
    sys.exit(0 if card["passed"] else 1)


if __name__ == "__main__":
    main()
