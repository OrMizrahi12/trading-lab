"""Simple vectorized backtester: SPY 5-minute bars, EMA(20) / EMA(50) trend rule.

Rule: be long while the 20-bar EMA is above the 50-bar EMA, flat otherwise, and
always flat at the close. A signal is read at a bar's close and filled at the
NEXT bar's open (the honest timing). Every bar is flat or long, so profit is
just position * (price change), computed for all bars at once with no loop.

Run: uv run -m backtests.vectorized

Paper research only. Nothing here touches a broker.
"""

from pathlib import Path

import pandas as pd

import indicators

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "SPY_5min.parquet"
FAST, SLOW = 20, 50
SHARES = 100
CAPITAL = 100_000.0
# Realistic costs: 1 cent/share commission per side, plus a 1 cent quoted spread.
# Bar opens are treated as the mid price (halfway between bid and ask), so each
# fill gives up half the spread: you buy at the ask, sell at the bid.
COMMISSION = 0.01
SPREAD = 0.01
COSTS_PER_SIDE = COMMISSION + SPREAD / 2


def load_bars(path: Path = CACHE) -> pd.DataFrame:
    return pd.read_parquet(path)


def backtest(
    bars: pd.DataFrame,
    lag: int = 1,
    cost_per_side: float = 0.0,
    shares: int = SHARES,
    capital: float = CAPITAL,
) -> dict:
    """Run the strategy; return trades, per-bar equity and summary stats.

    lag=1 is honest: the signal from bar i-1's close sets the position held
    from bar i's open. lag=0 is the look-ahead lie: bar i's own close decides
    the position held from bar i's open, a decision made with data from the future.
    cost_per_side is dollars per share paid on every fill (entry and exit).
    """
    df = bars[["open", "close"]].copy()
    day = df.index.tz_convert("America/New_York").date
    day = pd.Series(day, index=df.index)
    first_of_day = day != day.shift(1)
    last_of_day = day != day.shift(-1)

    fast = indicators.ema(df["close"], FAST)
    slow = indicators.ema(df["close"], SLOW)
    signal = (fast > slow).astype(int)  # NaN warm-up compares False, so flat

    # position held from this bar's open. Never carry a position across days.
    pos = signal.shift(lag, fill_value=0)
    pos[first_of_day] = 0

    # Each bar's position earns open -> next open, except the last bar of the
    # day, which is closed out at its own close ("always flat at the close").
    next_price = df["open"].shift(-1).where(~last_of_day, df["close"])
    gross = pos * (next_price - df["open"]) * shares

    entry = (pos == 1) & (pos.shift(1, fill_value=0) == 0)
    exit_ = (pos == 1) & ((pos.shift(-1, fill_value=0) == 0) | last_of_day)
    cost = (entry.astype(int) + exit_.astype(int)) * cost_per_side * shares
    pnl = gross - cost
    equity = capital + pnl.cumsum()

    # Trades: each run of consecutive long bars is one trade.
    run_id = (pos != pos.shift(1, fill_value=0)).cumsum()
    held = df.assign(pos=pos, pnl=pnl, run=run_id, exit_px=next_price)[pos == 1]
    g = held.groupby("run")
    trades = pd.DataFrame(
        {
            "entry_time": g.apply(lambda r: r.index[0], include_groups=False),
            "exit_time": g.apply(lambda r: r.index[-1] + pd.Timedelta(minutes=5), include_groups=False),
            "entry_px": g["open"].first(),
            "exit_px": g["exit_px"].last(),
            "pnl": g["pnl"].sum(),
        }
    ).reset_index(drop=True)

    drawdown = equity / equity.cummax() - 1
    stats = {
        "trades": len(trades),
        "total_return_%": (equity.iloc[-1] / capital - 1) * 100,
        "win_rate_%": (trades["pnl"] > 0).mean() * 100,
        "max_drawdown_%": drawdown.min() * 100,
        "avg_trade_$": trades["pnl"].mean(),
        "costs_paid_$": cost.sum(),
    }
    return {"trades": trades, "equity": equity, "stats": stats}


def main() -> None:
    bars = load_bars()
    runs = {
        "honest": backtest(bars, lag=1),
        "look-ahead": backtest(bars, lag=0),
        "honest + costs": backtest(bars, lag=1, cost_per_side=COSTS_PER_SIDE),
    }
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    print(f"SPY 5min, {span}, {SHARES} shares, ${CAPITAL:,.0f} start\n")

    honest = runs["honest"]["trades"]
    print("First 5 honest trades:")
    print(honest.head().to_string(), "\n")

    table = pd.DataFrame({k: v["stats"] for k, v in runs.items()})
    print(table.round(2).to_string())


if __name__ == "__main__":
    main()
