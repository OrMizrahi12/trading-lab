"""Portfolio backtester: several strategies trade ONE account at the same time.

`backtests/engine.py` runs one strategy on one symbol with its own money. This module replays
several (symbol, strategy) pairs, called sleeves, on a shared timeline against a single cash pool,
with account-wide rules on top. Fills, costs, stop handling, sizing and R are the same as
`engine.py` (decide at the close, fill at the next open, stop first, no overnight, no leverage),
so a one-sleeve portfolio with the rules switched off reproduces `run_backtest` exactly
(tests/test_portfolio.py checks that).

Account rules, both checked when an entry is about to fill:

- Daily loss limit: if the account is down `daily_loss_limit` (e.g. 2%) or more from the previous
  session's close, marked at the current open, no NEW entry is taken for the rest of that session.
  Positions already open keep their own stops and the end-of-day flatten; the limit does not
  force them shut.
- Trade cap: at most `max_trades_per_day` entries per session across all sleeves.

When several sleeves want to enter on the same bar, they are served in the order given. Position
size is risk_pct of the whole account's equity, capped by the cash that is actually free (no leverage).

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

import indicators
from backtests.engine import STARTING_EQUITY, CostModel
from backtests.sizing import RISK_PCT
from strategies.base import Entry, Exit, Position, Strategy, check_bracket


@dataclass
class Sleeve:
    name: str
    bars: pd.DataFrame
    strategy: Strategy


@dataclass
class PortfolioResult:
    trades: pd.DataFrame  # every sleeve's trades, with a `sleeve` column
    equity: pd.Series  # whole account, marked to the close of every bar
    blocked: dict = field(default_factory=dict)  # entries refused, by reason
    limit_days: int = 0  # sessions in which the daily loss limit stopped new entries


class _State:
    """One sleeve's open position and bookkeeping."""

    def __init__(self, sleeve: Sleeve, idx: pd.DatetimeIndex):
        b = sleeve.bars.loc[idx]
        self.sleeve = sleeve
        self.bars = b
        self.open, self.high, self.low, self.close = (b[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        self.pos: Position | None = None
        self.shares = 0
        self.entry_raw = self.risk_dollars = self.stop_risk = 0.0
        self.pending: Entry | Exit | None = None


def run_portfolio(
    sleeves: list[Sleeve],
    equity: float = STARTING_EQUITY,
    risk_pct: float = RISK_PCT,
    costs: CostModel = CostModel(),
    max_trades_per_day: int | None = None,
    daily_loss_limit: float | None = None,
    lookback_bars: int = 200,
) -> PortfolioResult:
    if lookback_bars < 80:
        raise ValueError("lookback_bars must cover at least one 78-bar session")
    idx = sleeves[0].bars.index
    for s in sleeves[1:]:
        idx = idx.intersection(s.bars.index)
    n = len(idx)
    session = np.asarray(idx.tz_convert(indicators.SESSION_TZ).date)
    states = [_State(s, idx) for s in sleeves]
    for s in sleeves:
        if hasattr(s.strategy, "reset"):
            s.strategy.reset()

    cash = float(equity)
    trades: list[dict] = []
    curve = np.empty(n)
    blocked = {"trade_cap": 0, "daily_loss_limit": 0, "no_cash": 0}
    limit_days = 0
    day_start_equity = cash
    entries_today = 0
    limit_hit = False

    def mark(px_of) -> float:
        return cash + sum(st.shares * px_of(st) for st in states if st.pos is not None)

    def close_position(st: _State, i: int, raw_px: float, reason: str) -> None:
        nonlocal cash
        fill = costs.sell_limit(raw_px) if reason == "target" else costs.sell(raw_px)
        sh, pos = st.shares, st.pos
        cash += sh * fill - sh * costs.commission_per_share
        net = sh * (fill - pos.entry_price) - 2 * sh * costs.commission_per_share
        gross = sh * (raw_px - st.entry_raw)
        trades.append({
            "sleeve": st.sleeve.name, "entry_time": pos.entry_time, "exit_time": idx[i],
            "entry_price": pos.entry_price, "exit_price": fill, "shares": sh, "reason": reason,
            "gross_pnl": gross, "costs": gross - net, "net_pnl": net,
            "stop_risk": st.stop_risk, "r_multiple": net / st.risk_dollars,
        })
        st.pos, st.shares = None, 0

    for i in range(n):
        new_session = i == 0 or session[i] != session[i - 1]
        if new_session:
            if i > 0:
                day_start_equity = curve[i - 1]
            else:
                day_start_equity = cash
            entries_today, limit_hit = 0, False

        # Nothing is carried overnight.
        for st in states:
            if st.pos is not None and new_session and i > 0:
                close_position(st, i, st.open[i], "overnight")
                st.pending = None

        # 1a. Exits first, so the cash they free is available to entries on the same bar.
        for st in states:
            if isinstance(st.pending, Exit) and st.pos is not None:
                close_position(st, i, st.open[i], st.pending.reason)
                st.pending = None

        # 1b. Entries, in sleeve order, subject to the account rules.
        for st in states:
            if not (isinstance(st.pending, Entry) and st.pos is None):
                st.pending = None
                continue
            pend, st.pending = st.pending, None
            if daily_loss_limit is not None and not limit_hit:
                if mark(lambda s: s.open[i]) / day_start_equity - 1 <= -daily_loss_limit:
                    limit_hit = True
                    limit_days += 1
            if limit_hit:
                blocked["daily_loss_limit"] += 1
                continue
            if max_trades_per_day is not None and entries_today >= max_trades_per_day:
                blocked["trade_cap"] += 1
                continue
            fill = costs.buy(st.open[i])
            risk_ps = (fill - pend.stop + 2 * costs.commission_per_share
                       + costs.spread / 2 + costs.slippage_per_share)
            if not (pend.stop < fill and risk_ps > 0):
                continue
            total = mark(lambda s: s.open[i])
            qty = min(math.floor(total * risk_pct / risk_ps),
                      math.floor(cash / (fill + costs.commission_per_share)))
            if qty <= 0:
                blocked["no_cash"] += 1
                continue
            st.shares, st.entry_raw, st.stop_risk = qty, st.open[i], qty * risk_ps
            st.risk_dollars = total * risk_pct  # fixed 1R, before the buy is paid for
            cash -= qty * fill + qty * costs.commission_per_share
            st.pos = Position(idx[i], fill, pend.stop, pend.target, 0)
            entries_today += 1

        # 2. Stops and targets inside this bar (the entry bar included).
        for st in states:
            if st.pos is not None:
                hit = check_bracket(st.pos, st.bars.iloc[i])
                if hit is not None:
                    close_position(st, i, hit[1], hit[0])

        # 3. Last bar closes everything; otherwise ask every strategy for its next decision.
        for st in states:
            if i == n - 1:
                if st.pos is not None:
                    close_position(st, i, st.close[i], "end_of_data")
                continue
            if st.pos is not None:
                st.pos = replace(st.pos, bars_held=st.pos.bars_held + 1)
            start = max(0, i + 1 - lookback_bars)
            intent = st.sleeve.strategy.on_bar(st.bars.iloc[start : i + 1], st.pos)
            if (isinstance(intent, Entry) and st.pos is None) or (
                isinstance(intent, Exit) and st.pos is not None
            ):
                st.pending = intent

        curve[i] = mark(lambda s: s.close[i])

    return PortfolioResult(
        pd.DataFrame(trades).sort_values("entry_time", ignore_index=True) if trades else pd.DataFrame(),
        pd.Series(curve, index=idx, name="equity"), blocked, limit_days,
    )
