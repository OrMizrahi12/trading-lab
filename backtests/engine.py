"""Event-driven backtest engine: runs any Strategy (strategies/base.py) bar by bar.

The strategy only decides; this engine does the fills, sizing and costs, so a
strategy is written once and also works in the live paper trader. Rules:

- A decision is made at a bar's close and filled at the NEXT bar's open.
- Stops and targets are checked inside every bar, including the entry bar, with
  `check_bracket`: if one bar touches both, the stop wins (the worse case). If
  the bar gaps through the stop (opens past it), the fill is the open, not the stop.
- Costs: commission (broker fee per share) on both sides; half the spread (the gap
  between the buy price and the sell price) on every fill, because bar prices are
  treated as the mid price; slippage (getting a slightly worse price than quoted)
  on market-type fills only (entry, stop, signal or time exit). A target is a
  limit order, so it pays spread but no slippage.
- Size: a `Sizer` (backtests/sizing.py) picks the shares. The default, fixed fractional, makes a
  stop-out lose `risk_pct` of equity, costs included. Whatever the sizer says, a position never
  costs more than `max_position_pct` of equity (no leverage). When a stop is tight the cap
  binds, the position is smaller than that, and a stop-out loses less than `risk_pct`.
- R: a trade's `r_multiple` is net P&L over a FIXED `risk_pct` of equity at entry (so 1R is
  always 1% of the account, even when the cap held the real risk lower). `stop_risk` is the
  dollars a stop-out would really have cost.

Run: uv run -m backtests.engine

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

import indicators
from backtests.vectorized import CACHE, COMMISSION, SPREAD, load_bars
from backtests.sizing import RISK_PCT, FixedFractional, Sizer
from strategies.base import Entry, Exit, Position, Strategy, check_bracket

STARTING_EQUITY = 100_000.0
SLIPPAGE = 0.01  # dollars per share, first guess for liquid SPY


@dataclass(frozen=True)
class CostModel:
    """Dollars per share. Defaults match backtests/vectorized.py plus 1 cent of slippage."""

    commission_per_share: float = COMMISSION
    spread: float = SPREAD
    slippage_per_share: float = SLIPPAGE

    def buy(self, px: float) -> float:
        """Price actually paid for a market buy at mid price `px`."""
        return px + self.spread / 2 + self.slippage_per_share

    def sell(self, px: float) -> float:
        """Price actually received for a market sell."""
        return px - self.spread / 2 - self.slippage_per_share

    def sell_limit(self, px: float) -> float:
        """A limit sell (the target) pays the spread but no slippage."""
        return px - self.spread / 2


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series  # marked to the close of every bar
    stats: dict = field(default_factory=dict)


def run_backtest(
    bars: pd.DataFrame,
    strategy: Strategy,
    equity: float = STARTING_EQUITY,
    risk_pct: float = RISK_PCT,
    costs: CostModel = CostModel(),
    max_position_pct: float = 1.0,
    lookback_bars: int | None = 200,
    sizer: Sizer | None = None,
) -> BacktestResult:
    """Replay `bars` through `strategy`, one closed bar at a time.

    `sizer` picks the share count (backtests/sizing.py); the default is `FixedFractional(risk_pct)`.
    `risk_pct` also stays the fixed yardstick for `r_multiple`, whichever sizer is used.

    `lookback_bars` limits how many closed bars the strategy is shown (None = all
    of them). It must cover at least one session so session-reset indicators such
    as VWAP see the whole day; a window is much faster than the full history.
    """
    if lookback_bars is not None and lookback_bars < 80:
        raise ValueError("lookback_bars must cover at least one 78-bar session")
    if hasattr(strategy, "reset"):
        strategy.reset()
    sizer = sizer or FixedFractional(risk_pct)
    sizer.prepare(bars)

    n = len(bars)
    idx = bars.index
    open_, high, low, close = (bars[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    session = np.asarray(idx.tz_convert(indicators.SESSION_TZ).date)

    cash = float(equity)
    pos: Position | None = None
    shares = 0
    entry_raw = risk_dollars = stop_risk = 0.0
    pending: Entry | Exit | None = None
    trades: list[dict] = []
    equity_curve = np.empty(n)
    bars_in_trade = 0

    def close_position(i: int, raw_px: float, reason: str) -> None:
        nonlocal cash, pos, shares
        if reason == "target":
            fill = costs.sell_limit(raw_px)
        else:
            fill = costs.sell(raw_px)
        cash += shares * fill - shares * costs.commission_per_share
        net = shares * (fill - pos.entry_price) - 2 * shares * costs.commission_per_share
        gross = shares * (raw_px - entry_raw)
        trades.append(
            {
                "entry_time": pos.entry_time,
                "exit_time": idx[i],
                "entry_price": pos.entry_price,
                "exit_price": fill,
                "shares": shares,
                "reason": reason,
                "gross_pnl": gross,
                "costs": gross - net,
                "net_pnl": net,
                "stop_risk": stop_risk,
                "r_multiple": net / risk_dollars,
            }
        )
        pos, shares = None, 0

    for i in range(n):
        # A position must not be carried into a new session; leave at that open.
        if pos is not None and session[i] != session[i - 1]:
            close_position(i, open_[i], "overnight")
            pending = None

        # 1. Fill what the strategy decided at the previous bar's close.
        if isinstance(pending, Exit) and pos is not None:
            close_position(i, open_[i], pending.reason)
        elif isinstance(pending, Entry) and pos is None:
            fill = costs.buy(open_[i])
            # Round-trip cost per share if the stop is hit, so a stop-out costs risk_pct.
            risk_ps = (fill - pending.stop + 2 * costs.commission_per_share
                       + costs.spread / 2 + costs.slippage_per_share)
            if pending.stop < fill and risk_ps > 0:
                qty = sizer.shares(equity=cash, fill=fill, risk_per_share=risk_ps,
                                   entry_time=idx[i], trades=trades)
                qty = min(qty, math.floor(cash * max_position_pct / fill))
                if qty > 0:
                    shares, entry_raw, stop_risk = qty, open_[i], qty * risk_ps
                    risk_dollars = cash * risk_pct  # fixed 1R, before the buy is paid for
                    cash -= shares * fill + shares * costs.commission_per_share
                    pos = Position(idx[i], fill, pending.stop, pending.target, 0)
        pending = None

        # 2. Did this bar hit the stop or target? (Includes the entry bar.)
        if pos is not None:
            hit = check_bracket(pos, bars.iloc[i])
            if hit is not None:
                close_position(i, hit[1], hit[0])

        # 3. Last bar: nothing can fill after it, so close at the final close.
        if i == n - 1:
            if pos is not None:
                close_position(i, close[i], "end_of_data")
        else:
            if pos is not None:
                pos = replace(pos, bars_held=pos.bars_held + 1)
                bars_in_trade += 1
            start = 0 if lookback_bars is None else max(0, i + 1 - lookback_bars)
            intent = strategy.on_bar(bars.iloc[start : i + 1], pos)
            if (isinstance(intent, Entry) and pos is None) or (
                isinstance(intent, Exit) and pos is not None
            ):
                pending = intent

        equity_curve[i] = cash + (shares * close[i] if pos is not None else 0.0)

    trades_df = pd.DataFrame(trades)
    curve = pd.Series(equity_curve, index=idx, name="equity")
    return BacktestResult(trades_df, curve, summarize(trades_df, curve, equity, bars_in_trade, n))


def summarize(trades: pd.DataFrame, curve: pd.Series, start_equity: float,
              bars_in_trade: int, n_bars: int) -> dict:
    if trades.empty:
        return {"trades": 0, "total_return_%": 0.0, "max_drawdown_%": 0.0}
    net = trades["net_pnl"]
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {
        "trades": len(trades),
        "win_rate_%": (net > 0).mean() * 100,
        "total_return_%": (curve.iloc[-1] / start_equity - 1) * 100,
        "profit_factor": wins / losses if losses > 0 else float("inf"),
        "avg_trade_$": net.mean(),
        "avg_R": trades["r_multiple"].mean(),
        "max_drawdown_%": (curve / curve.cummax() - 1).min() * 100,
        "costs_paid_$": trades["costs"].sum(),
        "gross_pnl_$": trades["gross_pnl"].sum(),
        "time_in_market_%": bars_in_trade / n_bars * 100,
        "exits": trades["reason"].value_counts().to_dict(),
    }


def main() -> None:
    from strategies.spy_vwap_reclaim import SpyVwapReclaim

    bars = load_bars(CACHE)
    result = run_backtest(bars, SpyVwapReclaim())
    span = f"{bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}"
    print(f"spy_vwap_reclaim on SPY 5min, {span}, ${STARTING_EQUITY:,.0f} start, "
          f"{RISK_PCT:.0%} risk per trade\n")
    for k, v in result.stats.items():
        print(f"{k:>18}: {v:.2f}" if isinstance(v, float) else f"{k:>18}: {v}")
    print("\nFirst 5 trades:")
    print(result.trades.head().round({"entry_price": 2, "exit_price": 2, "gross_pnl": 2,
                                      "costs": 2, "net_pnl": 2, "r_multiple": 2}).to_string())


if __name__ == "__main__":
    main()
