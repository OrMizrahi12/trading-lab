"""Backtest opening_range_breakout on SPY and QQQ 5min: N = 5 and 15 minutes, with and without VWAP.

2 symbols x 2 range lengths x 2 variants = 8 runs through the shared engine with the usual costs
and 1% risk per trade, no leverage, long only, flat before the close. Printed as one table, then
next to what the two Zarattini papers reported (their numbers are typed in below from published
summaries; they are not computed here). Run: uv run -m backtests.orb_comparison  (a minute or two)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from backtests.engine import RISK_PCT, STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import METRICS, compute_metrics, format_value
from backtests.vectorized import CACHE, load_bars
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

SYMBOLS = ["QQQ", "SPY"]
RANGES = [5, 15]
VARIANTS = {"plain": False, "above VWAP": True}
COLUMNS = ["trades", "net_profit", "cagr", "sharpe", "max_drawdown", "profit_factor",
           "win_rate", "avg_r", "exposure"]
LABELS = {m.key: m for m in METRICS}


def run_all() -> list[dict]:
    rows = []
    for symbol in SYMBOLS:
        bars = load_bars(CACHE.with_name(f"{symbol}_5min.parquet"))
        for minutes in RANGES:
            for variant, vwap in VARIANTS.items():
                params = OpeningRangeBreakoutParams(range_minutes=minutes, require_above_vwap=vwap)
                result = run_backtest(bars, OpeningRangeBreakout(params), costs=CostModel())
                m = compute_metrics(result.trades, result.equity, STARTING_EQUITY)
                rows.append({"symbol": symbol, "range": f"{minutes} min", "variant": variant,
                             "trades_df": result.trades, **m})
                print(f"done {symbol} {minutes}min {variant}", flush=True)
    return rows


def table(rows: list[dict]) -> str:
    head = ["Symbol", "Range", "Variant"] + [LABELS[c].label for c in COLUMNS]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        cells = [r["symbol"], r["range"], r["variant"]]
        cells += [format_value(LABELS[c], r[c]) for c in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    rows = run_all()
    bars = load_bars()
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    print(f"\n5min bars {span}, ${STARTING_EQUITY:,.0f} start, {RISK_PCT:.0%} risk per trade, "
          "long only, no leverage, flat before the close, usual costs (1c commission, 1c spread, "
          "1c slippage per share)\n")
    print(table(rows))


if __name__ == "__main__":
    main()
