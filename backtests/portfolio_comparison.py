"""Three strategies as one account: correlation of their daily results, then a combined run.

ORB on QQQ (15-minute range), Donchian breakout on SPY and Bollinger/RSI reversion on QQQ, all 5min,
usual costs, 1% risk per trade. Each runs alone on its own $100,000 through `run_backtest`; then all
three share one $100,000 account through `run_portfolio`, first with no account rules and then with a
2% daily loss limit and at most 3 entries a day. Prints the daily-result correlation matrix and one
table. Run: uv run -m backtests.portfolio_comparison  (a few minutes)

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import pandas as pd

import indicators
from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
from backtests.metrics import METRICS, compute_metrics, daily_returns, format_value
from backtests.portfolio import Sleeve, run_portfolio
from backtests.vectorized import CACHE, load_bars
from strategies.bollinger_rsi_reversion import BollingerRsiReversion
from strategies.donchian_breakout import DonchianBreakout
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams

DAILY_LOSS_LIMIT, MAX_TRADES_PER_DAY = 0.02, 3
COLUMNS = ["trades", "net_profit", "cagr", "sharpe", "sortino", "max_drawdown", "profit_factor",
           "win_rate", "avg_r", "exposure"]
LABELS = {m.key: m for m in METRICS}

SLEEVES = {  # name -> (symbol, strategy factory)
    "ORB QQQ 15min": ("QQQ", lambda: OpeningRangeBreakout(OpeningRangeBreakoutParams(range_minutes=15))),
    "Donchian SPY": ("SPY", DonchianBreakout),
    "Bollinger QQQ": ("QQQ", BollingerRsiReversion),
}


def make_sleeves() -> list[Sleeve]:
    bars = {s: load_bars(CACHE.with_name(f"{s}_5min.parquet")) for s in {v[0] for v in SLEEVES.values()}}
    return [Sleeve(name, bars[sym], make()) for name, (sym, make) in SLEEVES.items()]


def table(rows: dict[str, dict]) -> str:
    head = ["Account"] + [LABELS[c].label for c in COLUMNS]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for name, m in rows.items():
        lines.append("| " + " | ".join([name] + [format_value(LABELS[c], m[c]) for c in COLUMNS]) + " |")
    return "\n".join(lines)


def main() -> None:
    costs = CostModel()
    sleeves = make_sleeves()
    alone, rows = {}, {}
    for s in sleeves:
        res = run_backtest(s.bars, s.strategy, costs=costs)
        alone[s.name] = res
        rows[s.name + " (alone)"] = compute_metrics(res.trades, res.equity, STARTING_EQUITY)
        print(f"done {s.name} alone", flush=True)

    free = run_portfolio(sleeves, costs=costs)
    ruled = run_portfolio(sleeves, costs=costs, daily_loss_limit=DAILY_LOSS_LIMIT,
                          max_trades_per_day=MAX_TRADES_PER_DAY)
    rows["Combined, no account rules"] = compute_metrics(free.trades, free.equity, STARTING_EQUITY)
    rows["Combined, 2% limit + 3 trades/day"] = compute_metrics(ruled.trades, ruled.equity, STARTING_EQUITY)

    daily = pd.DataFrame({n: daily_returns(r.equity, STARTING_EQUITY) for n, r in alone.items()})
    span = f"{daily.index[0]} to {daily.index[-1]}"
    print(f"\n{len(daily)} sessions, {span}; each strategy alone on ${STARTING_EQUITY:,.0f}, usual costs\n")
    print("Correlation of daily results (all sessions, flat days count as 0):")
    print(daily.corr().round(2).to_string())
    active = daily.ne(0)
    print("\nSessions with a non-zero daily result:")
    print(active.sum().to_string())
    print(f"sessions where all three were active: {int(active.all(axis=1).sum())}")
    both = daily[active.all(axis=1)]
    if len(both) > 2:
        print("\nCorrelation on sessions where all three were active:")
        print(both.corr().round(2).to_string())
    print("\nWorst sessions (daily return):")
    for n in daily:
        print(f"  {n:>16}: {daily[n].min():.2%}")
    for name, r in (("no rules", free), ("2% limit + 3/day", ruled)):
        print(f"  {'combined ' + name:>16}: {daily_returns(r.equity, STARTING_EQUITY).min():.2%}")

    print(f"\nRules that bit (combined run with rules): entries refused {ruled.blocked}, "
          f"sessions the 2% limit stopped new entries: {ruled.limit_days}")
    print("\nPer sleeve: trades alone / combined no rules / combined with rules, and average position "
          "as % of the $100k start")
    for n, r in alone.items():
        t = r.trades
        line = [f"{len(t)}"]
        pos = [(t.shares * t.entry_price).mean() / STARTING_EQUITY]
        for c in (free, ruled):
            ct = c.trades[c.trades["sleeve"] == n]
            line.append(f"{len(ct)}")
            pos.append((ct.shares * ct.entry_price).mean() / STARTING_EQUITY)
        print(f"  {n:>16}: trades {' / '.join(line)}   avg position {' / '.join(f'{x:.0%}' for x in pos)}")
    print("\n" + table(rows))


if __name__ == "__main__":
    main()
