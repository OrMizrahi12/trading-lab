"""H3: the frozen ORB (15-minute range, 1R target) on years its settings never saw.

Pre-registered in research/cards/h3_orb_unseen_years.md. Everything here is fixed by that
card: settings, span (sessions 2019-10-01 to 2024-09-30), symbols (QQQ, SPY), costs, sizing and
the PASS / PARTIAL / FAIL rules. Takes no command-line settings. Reads only data/oos/ (bars from
before the research cache began). Not the judge, so nothing goes in the trial ledger.

Run once: uv run -m backtests.h3_unseen_years   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""
from pathlib import Path

import exchange_calendars as xc
import numpy as np
import pandas as pd

import indicators
from backtests.engine import STARTING_EQUITY, run_backtest
from backtests.metrics import compute_metrics, daily_returns
from reports.orb_stress_test import monte_carlo, pct, scaled, trade_returns
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

OOS = Path(__file__).resolve().parent.parent / "data" / "oos"
SYMBOLS = ["QQQ", "SPY"]  # QQQ is the primary symbol
SPAN_START, SPAN_END = "2019-10-01", "2024-09-30"
PARAMS = dict(range_minutes=15, target_r=1.0, require_above_vwap=False, max_hold_bars=None)
BUCKETS = [2019, 2020, 2021, 2022, 2023, 2024]
PICKED_ON = {"sharpe": 1.77, "trades": 354, "max_drawdown": -0.052}  # QQQ 2024-10 to 2026-04, from the card
TZ = indicators.SESSION_TZ


def load_span(symbol: str) -> pd.DataFrame:
    bars = pd.read_parquet(OOS / f"{symbol}_5min.parquet")
    day = bars.index.tz_convert(TZ).normalize().tz_localize(None)
    return bars[(day >= pd.Timestamp(SPAN_START)) & (day <= pd.Timestamp(SPAN_END))]


def backtest(bars: pd.DataFrame, k: float = 1.0):
    strategy = OpeningRangeBreakout(OpeningRangeBreakoutParams(**PARAMS))
    return run_backtest(bars, strategy, costs=scaled(k))


def session_counts(bars: pd.DataFrame) -> tuple[int, int]:
    """(sessions in the span per the NYSE calendar, sessions whose 15-minute range is complete)."""
    sessions = xc.get_calendar("XNYS").sessions_in_range(SPAN_START, SPAN_END)
    local = bars.index.tz_convert(TZ)
    first3 = pd.Series(1, index=local)[(local.hour == 9) & (local.minute < 45) & (local.minute >= 30)]
    n = first3.groupby(first3.index.date).sum()
    complete = {d for d, c in n.items() if c == 3}
    return len(sessions), sum(1 for s in sessions if s.date() in complete)


def year_table(res) -> pd.DataFrame:
    eq = res.equity
    year_of_bar = eq.index.tz_convert(TZ).year
    year_end = eq.groupby(year_of_bar).last()
    daily = daily_returns(eq, STARTING_EQUITY)
    dyear = pd.Index([d.year for d in daily.index])
    entries = res.trades.entry_time.dt.tz_convert(TZ).dt.year
    rows, prev = [], STARTING_EQUITY
    for y in BUCKETS:
        r = daily[dyear == y]
        sharpe = r.mean() / r.std(ddof=1) * np.sqrt(252) if len(r) > 1 and r.std(ddof=1) > 0 else float("nan")
        rows.append({"year": y, "trades": int((entries == y).sum()),
                     "net_pct": float(year_end[y] / prev - 1), "sharpe": float(sharpe)})
        prev = float(year_end[y])
    return pd.DataFrame(rows)


def buy_and_hold(bars: pd.DataFrame) -> dict:
    close = bars.close.groupby(bars.index.tz_convert(TZ).date).last()
    total = float(bars.close.iloc[-1] / bars.open.iloc[0] - 1)
    years = (bars.index[-1] - bars.index[0]).total_seconds() / (365.25 * 86400)
    dd = float((close / close.cummax() - 1).min())
    return {"total": total, "cagr": (1 + total) ** (1 / years) - 1, "max_drawdown": dd}


def verdict(m: dict, mc_dd_p5: float, years: pd.DataFrame) -> str:
    """The card's rules, applied to QQQ."""
    up = int((years.net_pct > 0).sum())
    if m["sharpe"] >= 1.0 and m["net_profit"] > 0 and mc_dd_p5 >= -0.20 and up >= 4:
        return "PASS"
    if m["sharpe"] >= 0.5 and m["net_profit"] > 0 and up >= 3:
        return "PARTIAL"
    return "FAIL"


def run_symbol(symbol: str) -> dict:
    bars = load_span(symbol)
    print(f"\n===== {symbol}: {len(bars):,} bars, {bars.index[0]} -> {bars.index[-1]} =====", flush=True)
    in_span, complete = session_counts(bars)
    print(f"Sessions in span {in_span}, with a complete opening range {complete}, skipped {in_span - complete}")
    base = backtest(bars)
    m = compute_metrics(base.trades, base.equity, STARTING_EQUITY)
    rets = trade_returns(base)
    mc = monte_carlo(rets)
    years = year_table(base)
    print("Metrics (usual costs):")
    for key in ("trades", "win_rate", "avg_r", "profit_factor", "net_profit", "cagr", "max_drawdown",
                "sharpe", "sortino", "expectancy", "exposure"):
        print(f"  {key}: {m[key]}")
    print(f"Monte Carlo (5,000 draws, seed 42): P5 max drawdown {pct(mc['dd'], 5):.4f}, "
          f"P5 final return {pct(mc['final'], 5):.4f}, median final {pct(mc['final'], 50):.4f}, "
          f"real max drawdown (trade to trade) {mc['real_dd']:.4f}")
    print("Calendar years:")
    print(years.round(4).to_string(index=False))
    print("Cost check:")
    costs = {}
    for k in (0, 2):
        r = backtest(bars, k)
        mk = compute_metrics(r.trades, r.equity, STARTING_EQUITY)
        costs[k] = mk
        print(f"  x{k}: net profit {mk['net_profit']:.2f}, Sharpe {mk['sharpe']:.4f}, trades {mk['trades']}")
    bh = buy_and_hold(bars)
    print(f"Buy and hold: total {bh['total']:.4f}, CAGR {bh['cagr']:.4f}, max drawdown (daily closes) {bh['max_drawdown']:.4f}")
    return {"m": m, "mc_dd_p5": pct(mc["dd"], 5), "years": years}


def main() -> None:
    out = {s: run_symbol(s) for s in SYMBOLS}
    q, s = out["QQQ"], out["SPY"]
    print("\n===== Side by side, QQQ =====")
    print(f"  picked on (2024-10 to 2026-04): Sharpe {PICKED_ON['sharpe']}, trades {PICKED_ON['trades']}, "
          f"max drawdown {PICKED_ON['max_drawdown']:.1%}")
    print(f"  unseen years: Sharpe {q['m']['sharpe']:.2f}, trades {q['m']['trades']}, "
          f"max drawdown {q['m']['max_drawdown']:.1%}")
    print(f"\nQQQ buckets profitable: {int((q['years'].net_pct > 0).sum())} of {len(BUCKETS)}")
    print(f"VERDICT (QQQ, by the card's rules): {verdict(q['m'], q['mc_dd_p5'], q['years'])}")
    if q["m"]["sharpe"] > 1.5:
        print("NOTE: Sharpe above 1.5 is the card's 'surprise, double-check for data or look-ahead error'.")
    print(f"SPY verdict by the same rules (does not count): {verdict(s['m'], s['mc_dd_p5'], s['years'])}; "
          f"net profit SPY {s['m']['net_profit']:.2f} vs QQQ {q['m']['net_profit']:.2f}")


if __name__ == "__main__":
    main()
