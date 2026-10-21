"""Backtest donchian_breakout and ema_trend on SPY and QQQ 5min, all day vs. no midday entries.

2 strategies x 2 symbols x 2 entry windows = 8 runs through the shared engine with the usual
costs (CostModel defaults) and 1% risk per trade, printed as one table. The midday variant
allows no new entries whose fill lands from 11:30 to 14:00; exits still work.
Run: uv run -m backtests.trend_comparison   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from dataclasses import replace
from datetime import time

from backtests.engine import RISK_PCT, STARTING_EQUITY, run_backtest
from backtests.metrics import METRICS, compute_metrics, format_value
from backtests.vectorized import CACHE, load_bars
from strategies.donchian_breakout import DonchianBreakout, DonchianBreakoutParams
from strategies.ema_trend import EmaTrend, EmaTrendParams

SYMBOLS = ["SPY", "QQQ"]
STRATEGIES = {
    "donchian_breakout": (DonchianBreakout, DonchianBreakoutParams()),
    "ema_trend": (EmaTrend, EmaTrendParams()),
}
WINDOWS = {
    "all day": {},
    "no 11:30-14:00": {"no_entry_from": time(11, 30), "no_entry_until": time(14, 0)},
}
COLUMNS = ["trades", "net_profit", "cagr", "sharpe", "max_drawdown", "profit_factor",
           "win_rate", "avg_r", "exposure"]
LABELS = {m.key: m for m in METRICS}


def run_all() -> list[dict]:
    rows = []
    for symbol in SYMBOLS:
        bars = load_bars(CACHE.with_name(f"{symbol}_5min.parquet"))
        for name, (cls, params) in STRATEGIES.items():
            for window, override in WINDOWS.items():
                result = run_backtest(bars, cls(replace(params, **override)))
                m = compute_metrics(result.trades, result.equity, STARTING_EQUITY)
                rows.append({"strategy": name, "symbol": symbol, "window": window, **m})
                print(f"done {name} {symbol} {window}", flush=True)
    return rows


def table(rows: list[dict]) -> str:
    head = ["Strategy", "Symbol", "Entries"] + [LABELS[c].label for c in COLUMNS]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        cells = [r["strategy"], r["symbol"], r["window"]]
        cells += [format_value(LABELS[c], r[c]) for c in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    rows = run_all()
    bars = load_bars()
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    print(f"\n5min bars {span}, ${STARTING_EQUITY:,.0f} start, {RISK_PCT:.0%} risk per trade, "
          "engine default costs, long only, flat before the close\n")
    print(table(rows))


if __name__ == "__main__":
    main()
