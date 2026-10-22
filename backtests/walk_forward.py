"""Walk-forward analysis of the opening range breakout on QQQ 5min.

Walk-forward means the strategy is never judged on data it was tuned on. The 24 months are cut into
rolling windows: tune on 6 months, trade the next 2 months with the settings that won, then slide
everything forward 2 months and repeat (9 windows, 18 traded months). Inside each tuning window
Optuna (TPE) picks the range length (5-60 min in 5 min steps) and the profit target (0.5-10 R) with
12 trials; the objective is the Sharpe ratio (return per unit of day-to-day swing) from the shared
engine with usual costs. A trial needs at least 25 trades (the 100 of optimize.py scaled to 6 of 24
months). The last entry time stays at the default 15:30. The traded months are chained into one
out-of-sample equity curve (each window starts with the account value the previous one ended on) and
compared with the fixed 15 min / 1R settings from backtests.optimize on the same months, and with
buy-and-hold QQQ (buy at the first traded open, never sell).

Stability check: every tuning window is also tuned with 4 more Optuna seeds. If the pick changes
with the seed, 12 trials are too few for the choice to mean anything. Only the seed-7 pick is traded.
Windows are cut on New York session dates. The chart goes to reports/walk_forward_orb.html.
Run: uv run -m backtests.walk_forward   (a few minutes; windows run in parallel)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import optuna
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import TRADING_DAYS, compute_metrics, daily_returns, drawdown
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "QQQ"
TUNE_MONTHS, TRADE_MONTHS = 6, 2
N_TRIALS = 12
N_STARTUP = 5  # random trials before TPE starts guiding (Optuna's default of 10 would leave 2 guided trials)
MIN_TRADES = 25
SEED = 7
EXTRA_SEEDS = [1, 2, 3, 4]
FIXED = (15, 1.0)  # range minutes, target in R: the grid winner from backtests.optimize
OUT = ROOT / "reports/walk_forward_orb.html"
COLORS = {"walk": "#2a78d6", "fixed": "#eb6834", "hold": "#6b6a66"}


def session_days(bars: pd.DataFrame) -> pd.DatetimeIndex:
    return bars.index.tz_convert("America/New_York").tz_localize(None).normalize()


def make_windows(bars: pd.DataFrame) -> list[dict]:
    start = session_days(bars)[0]
    # 24 months at most; less when the data stops sooner (the last six months are held out), so no window is empty
    end = min(start + pd.DateOffset(months=24), session_days(bars)[-1] + pd.Timedelta(days=1))
    out, k = [], 0
    while start + pd.DateOffset(months=TUNE_MONTHS + TRADE_MONTHS * (k + 1)) <= end:
        t0 = start + pd.DateOffset(months=TRADE_MONTHS * k)
        t1 = start + pd.DateOffset(months=TUNE_MONTHS + TRADE_MONTHS * k)
        t2 = start + pd.DateOffset(months=TUNE_MONTHS + TRADE_MONTHS * (k + 1))
        out.append({"k": k, "tune": (t0, t1), "trade": (t1, t2)})
        k += 1
    out[-1]["trade"] = (out[-1]["trade"][0], pd.Timestamp.max)  # keep the final session(s)
    return out


def cut(bars: pd.DataFrame, span: tuple) -> pd.DataFrame:
    day = session_days(bars)
    return bars[(day >= span[0]) & (day < span[1])]


def run(bars: pd.DataFrame, range_min: int, target: float, equity: float = STARTING_EQUITY):
    params = OpeningRangeBreakoutParams(range_minutes=range_min, target_r=target)
    return run_backtest(bars, OpeningRangeBreakout(params), equity=equity, costs=CostModel())


def tune(args: tuple) -> dict:
    """Optuna on one tuning window; returns the pick, or the fixed settings if no trial was valid."""
    k, span, seed = args
    bars = cut(load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet")), span)
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        rng = trial.suggest_int("range", 5, 60, step=5)
        tgt = trial.suggest_float("target", 0.5, 10.0)
        res = run(bars, rng, tgt)
        trial.set_user_attr("trades", len(res.trades))
        if res.trades.empty:
            return 0.0
        return float(np.nan_to_num(compute_metrics(res.trades, res.equity, STARTING_EQUITY)["sharpe"]))

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(
        seed=seed, n_startup_trials=N_STARTUP,
        constraints_func=lambda t: (MIN_TRADES - t.user_attrs["trades"],)))  # <= 0 is allowed
    study.optimize(objective, n_trials=N_TRIALS)
    ok = [t for t in study.trials if t.user_attrs["trades"] >= MIN_TRADES]
    if not ok:
        return {"k": k, "seed": seed, "range": FIXED[0], "target": FIXED[1], "tune_sharpe": float("nan"), "fallback": True}
    b = max(ok, key=lambda t: t.value)
    return {"k": k, "seed": seed, "range": b.params["range"], "target": b.params["target"],
            "tune_sharpe": b.value, "fallback": False, "valid": len(ok)}


def stitch(parts: list) -> tuple[pd.DataFrame, pd.Series]:
    return pd.concat([p.trades for p in parts], ignore_index=True), pd.concat([p.equity for p in parts])


def walk(bars: pd.DataFrame, windows: list[dict], picks: list[tuple[int, float]]) -> tuple[list, pd.DataFrame, pd.Series]:
    """Trade each window with its pick, chaining the account value from one window to the next."""
    parts, eq = [], STARTING_EQUITY
    for w, (rng, tgt) in zip(windows, picks):
        res = run(cut(bars, w["trade"]), rng, tgt, equity=eq)
        eq = float(res.equity.iloc[-1])
        parts.append(res)
    trades, equity = stitch(parts)
    return parts, trades, equity


def buy_and_hold(bars: pd.DataFrame) -> pd.Series:
    shares = STARTING_EQUITY / bars["open"].iloc[0]
    return shares * bars["close"]


def summary(equity: pd.Series, trades: pd.DataFrame | None) -> dict:
    m = compute_metrics(trades if trades is not None else pd.DataFrame(), equity, STARTING_EQUITY)
    if trades is None:  # buy-and-hold has no trades; the generic metrics still work on the curve
        r = daily_returns(equity, STARTING_EQUITY)
        m["sharpe"] = r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)
    m["total_return"] = float(equity.iloc[-1] / STARTING_EQUITY - 1)
    return m


def chart(windows, bars_oos, curves, picks) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, row_heights=[0.68, 0.32], vertical_spacing=0.1, shared_xaxes=True,
                        subplot_titles=("Account value, traded months only (out of sample)",
                                        "Settings Optuna chose in each window"))
    names = {"walk": "Walk-forward (re-tuned each window)", "fixed": "Fixed 15 min / 1R", "hold": "Buy and hold QQQ"}
    for key, eq in curves.items():
        daily = eq.groupby(eq.index.tz_convert("America/New_York").date).last()
        fig.add_trace(go.Scatter(x=list(daily.index), y=daily.values, name=names[key], mode="lines",
                                 line=dict(color=COLORS[key], width=2),
                                 hovertemplate="%{x}<br>$%{y:,.0f}<extra>" + names[key] + "</extra>"), row=1, col=1)
    fig.add_hline(y=STARTING_EQUITY, line_color="#888", line_width=1, line_dash="dot", row=1, col=1)
    mids = [w["trade"][0] + (min(w["trade"][1], bars_oos.index[-1].tz_localize(None)) - w["trade"][0]) / 2 for w in windows]
    for i, w in enumerate(windows):
        for row in (1, 2):
            fig.add_vline(x=w["trade"][0], line_color="#ccc", line_width=1, row=row, col=1)
    fig.add_trace(go.Scatter(x=mids, y=[p[0] for p in picks], mode="lines+markers", name="Range length (min, left axis)",
                             line=dict(color="#2a78d6"), hovertemplate="%{y} min<extra>range</extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=mids, y=[p[1] for p in picks], mode="lines+markers", name="Profit target (R, right axis)",
                             line=dict(color="#1baf7a"), xaxis="x2", yaxis="y3", hovertemplate="%{y:.1f}R<extra>target</extra>"))
    fig.update_layout(template="plotly_white", height=760, hovermode="x unified",
                      yaxis=dict(tickprefix="$", tickformat=",.0f", title="Account value"),
                      yaxis2=dict(title="Range (min)", range=[0, 65]),
                      yaxis3=dict(title="Target (R)", overlaying="y2", side="right", range=[0, 10.5], showgrid=False),
                      legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=70, r=70, t=70, b=40))
    return fig


def main() -> None:
    bars = load_bars(CACHE.with_name(f"{SYMBOL}_5min.parquet"))
    windows = make_windows(bars)
    jobs = [(w["k"], w["tune"], s) for w in windows for s in [SEED] + EXTRA_SEEDS]
    with ProcessPoolExecutor() as pool:
        tuned = pd.DataFrame(list(pool.map(tune, jobs)))
    main_picks = tuned[tuned.seed == SEED].sort_values("k")
    picks = [(int(r.range), float(r.target)) for r in main_picks.itertuples()]

    parts, wf_trades, wf_equity = walk(bars, windows, picks)
    fx_parts, fx_trades, fx_equity = walk(bars, windows, [FIXED] * len(windows))
    oos = pd.concat([cut(bars, w["trade"]) for w in windows])
    hold = buy_and_hold(oos)

    print(f"{SYMBOL} ORB walk-forward: tune {TUNE_MONTHS} months, trade {TRADE_MONTHS}, roll {TRADE_MONTHS}; "
          f"Optuna {N_TRIALS} trials ({N_STARTUP} random first), Sharpe objective, >= {MIN_TRADES} trades, usual costs.\n")
    print(f"{'#':<3}{'tuned on':<25}{'traded':<25}{'pick':<14}{'tune':>6}{'walk':>7}{'fixed':>7}  (Sharpe; trade-window columns are out of sample)")
    for w, p, part, fx, row in zip(windows, picks, parts, fx_parts, main_picks.itertuples()):
        sh = lambda r: np.nan_to_num(compute_metrics(r.trades, r.equity, float(r.equity.iloc[0]))["sharpe"])
        end = min(w["trade"][1], bars.index[-1].tz_localize(None)) - pd.Timedelta(days=1)
        print(f"{w['k'] + 1:<3}{w['tune'][0]:%Y-%m-%d} to {w['tune'][1] - pd.Timedelta(days=1):%m-%d}  "
              f"{w['trade'][0]:%Y-%m-%d} to {end:%m-%d}  {p[0]:>2}min/{p[1]:>4.1f}R   {row.tune_sharpe:>5.2f}{sh(part):>7.2f}{sh(fx):>7.2f}"
              + ("  (fallback: no valid trial)" if row.fallback else ""))

    print(f"\nStitched out-of-sample result, {oos.index[0]:%Y-%m-%d} to {oos.index[-1]:%Y-%m-%d}:")
    rows = {"Walk-forward": summary(wf_equity, wf_trades), "Fixed 15min/1R": summary(fx_equity, fx_trades),
            "Buy and hold": summary(hold, None)}
    print(f"{'':<16}{'return':>8}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'trades':>8}{'PF':>6}{'exposure':>10}")
    for name, m in rows.items():
        pf = m["profit_factor"]
        print(f"{name:<16}{m['total_return']:>8.1%}{m['cagr']:>8.1%}{m['sharpe']:>8.2f}{m['max_drawdown']:>8.1%}"
              f"{int(m['trades']) if name != 'Buy and hold' else 0:>8}{'' if np.isnan(pf) else f'{pf:.2f}':>6}"
              f"{'' if np.isnan(m['exposure']) else f'{m['exposure']:.0%}':>10}")

    r, t = main_picks["range"], main_picks["target"]
    print("\nParameter stability (seed 7, the picks that were traded):")
    print(f"  range: {sorted(r.astype(int).tolist())}  -> {r.nunique()} different values in {len(r)} windows, "
          f"changed at {(r.diff().abs() > 0).sum()} of {len(r) - 1} roll-forwards, "
          f"median step when it changed {r.diff().abs()[r.diff().abs() > 0].median():.0f} min")
    print(f"  target (R): {sorted(round(x, 1) for x in t)}  -> min {t.min():.1f}, median {t.median():.1f}, max {t.max():.1f}, "
          f"std {t.std():.1f}")
    print("  Same window, other seeds (does the pick depend on Optuna's dice?):")
    print(f"  {'#':<3}" + "".join(f"seed {s:<10}" for s in [SEED] + EXTRA_SEEDS) + "  range spread / target spread")
    for k in sorted(tuned.k.unique()):
        g = tuned[tuned.k == k].set_index("seed").loc[[SEED] + EXTRA_SEEDS]
        print(f"  {k + 1:<3}" + "".join(f"{int(a):>2}m/{b:>4.1f}R   " for a, b in zip(g['range'], g['target']))
              + f"  {int(g['range'].max() - g['range'].min())} min / {g['target'].max() - g['target'].min():.1f}R")
    print(f"  across all {len(tuned)} tunings: range used {tuned['range'].nunique()} different values, "
          f"15 min picked {(tuned['range'] == 15).mean():.0%} of the time, "
          f"median target {tuned['target'].median():.1f}R")

    fig = chart(windows, oos, {"walk": wf_equity, "fixed": fx_equity, "hold": hold}, picks)
    OUT.write_text(f"<!doctype html><meta charset='utf-8'><title>Walk-forward ORB QQQ</title>"
                   f"<body style='font:15px system-ui;max-width:1200px;margin:24px auto;padding:0 16px'>"
                   f"<h1 style='font-size:22px'>Walk-forward: QQQ opening range breakout</h1>"
                   f"<p>Tune on {TUNE_MONTHS} months, trade the next {TRADE_MONTHS}, roll forward {TRADE_MONTHS}. "
                   f"All three lines start at ${STARTING_EQUITY:,.0f} on the first traded day. Vertical lines are re-tuning points.</p>"
                   + fig.to_html(full_html=False, include_plotlyjs=True), encoding="utf-8")
    print(f"\nchart -> {OUT}")


if __name__ == "__main__":
    main()
