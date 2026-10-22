"""Backtest bollinger_rsi_reversion and vwap_stretch_reversion on SPY and QQQ 5min at three cost levels.

2 strategies x 2 symbols x 3 cost levels = 12 runs through the shared engine with 1% risk per
trade, printed as one table. Cost levels: zero (no commission, spread or slippage), usual (the
engine's CostModel defaults) and double (every cost component twice the usual). Mean-reversion
trades are small and frequent, so costs matter more here than for the trend strategies.
Run: uv run -m backtests.reversion_comparison   (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

from backtests.engine import RISK_PCT, STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import METRICS, compute_metrics, format_value
from backtests.vectorized import CACHE, load_bars
from strategies.bollinger_rsi_reversion import BollingerRsiReversion
from strategies.vwap_stretch_reversion import VwapStretchReversion

SYMBOLS = ["SPY", "QQQ"]
STRATEGIES = {
    "bollinger_rsi_reversion": BollingerRsiReversion,
    "vwap_stretch_reversion": VwapStretchReversion,
}
USUAL = CostModel()
COSTS = {
    "zero": CostModel(commission_per_share=0.0, spread=0.0, slippage_per_share=0.0),
    "usual": USUAL,
    "double": CostModel(
        commission_per_share=2 * USUAL.commission_per_share,
        spread=2 * USUAL.spread,
        slippage_per_share=2 * USUAL.slippage_per_share,
    ),
}
COLUMNS = ["trades", "net_profit", "cagr", "sharpe", "max_drawdown", "profit_factor",
           "win_rate", "avg_r", "exposure"]
LABELS = {m.key: m for m in METRICS}


def run_all() -> list[dict]:
    rows = []
    for symbol in SYMBOLS:
        bars = load_bars(CACHE.with_name(f"{symbol}_5min.parquet"))
        for name, cls in STRATEGIES.items():
            for level, costs in COSTS.items():
                result = run_backtest(bars, cls(), costs=costs)
                m = compute_metrics(result.trades, result.equity, STARTING_EQUITY)
                rows.append({"strategy": name, "symbol": symbol, "costs": level, **m})
                print(f"done {name} {symbol} {level}", flush=True)
    return rows


def table(rows: list[dict]) -> str:
    head = ["Strategy", "Symbol", "Costs"] + [LABELS[c].label for c in COLUMNS]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        cells = [r["strategy"], r["symbol"], r["costs"]]
        cells += [format_value(LABELS[c], r[c]) for c in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    rows = run_all()
    bars = load_bars()
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    print(f"\n5min bars {span}, ${STARTING_EQUITY:,.0f} start, {RISK_PCT:.0%} risk per trade, "
          "long only, flat before the close")
    print("costs: zero = none; usual = engine defaults (1c commission, 1c spread, 1c slippage "
          "per share); double = twice each\n")
    print(table(rows))


if __name__ == "__main__":
    main()
