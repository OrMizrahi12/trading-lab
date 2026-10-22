"""Position sizers: how many shares to buy once the strategy has said "enter".

The engine asks a sizer for a share count, then applies the no-leverage cap itself (a
position never costs more than the account holds), so every sizer is capped the same way.

- `FixedFractional`: risk a fixed share of the account (default 1%) on every trade. The
  stop is the "risk": shares = equity x 1% / (entry price - stop price + costs).
- `VolatilityTarget`: size so that one normal day's move in the stock (the daily ATR, the
  Average True Range: the typical distance price travels in a day, gaps included) costs
  1% of the account: shares = equity x 1% / daily ATR. The strategy's stop is ignored; a
  jumpy stock gets a small position and a calm one a big position.
- `FractionalKelly`: risk the share of the account that the Kelly formula suggests, scaled
  down and capped, using only trades that were already closed when this entry happens.

Anything that needs history (daily ATR, past trades) looks backwards only, so there is no
look-ahead. Until a sizer has enough history it falls back to `FixedFractional`.

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import math
from dataclasses import dataclass

import pandas as pd

import indicators

RISK_PCT = 0.01  # lose 1% of equity if the stop is hit (project default)


class Sizer:
    """Base class. `shares` returns a whole number of shares (0 = skip the trade)."""

    name = "sizer"

    def prepare(self, bars: pd.DataFrame) -> None:
        """Called once before a run, with every bar, for sizers that need a lookup table."""

    def shares(self, *, equity: float, fill: float, risk_per_share: float,
               entry_time: pd.Timestamp, trades: list[dict]) -> int:
        raise NotImplementedError


@dataclass
class FixedFractional(Sizer):
    """Lose `risk_pct` of equity if the stop is hit."""

    risk_pct: float = RISK_PCT
    name = "Fixed fractional"

    def shares(self, *, equity, fill, risk_per_share, entry_time, trades) -> int:
        return math.floor(equity * self.risk_pct / risk_per_share)


@dataclass
class VolatilityTarget(Sizer):
    """A normal day's move (the daily ATR) costs `risk_pct` of equity.

    The ATR for a session uses only sessions that closed before it. The first `atr_days`
    sessions have no ATR yet, so those trades are sized like `FixedFractional`.
    """

    risk_pct: float = RISK_PCT
    atr_days: int = 14
    name = "Volatility (daily ATR)"

    def prepare(self, bars: pd.DataFrame) -> None:
        day = pd.Series(bars.index.tz_convert(indicators.SESSION_TZ).date, index=bars.index)
        g = bars.groupby(day.values)
        daily = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(),
                              "close": g["close"].last()})
        if len(daily) <= self.atr_days:  # too little history for any ATR
            self._atr = {}
            return
        # shift(1): the value for a session is the ATR as of the previous session's close.
        self._atr = indicators.atr(daily["high"], daily["low"], daily["close"],
                                   self.atr_days).shift(1).to_dict()

    def shares(self, *, equity, fill, risk_per_share, entry_time, trades) -> int:
        atr = self._atr.get(entry_time.tz_convert(indicators.SESSION_TZ).date())
        if atr is None or math.isnan(atr) or atr <= 0:
            return math.floor(equity * self.risk_pct / risk_per_share)
        return math.floor(equity * self.risk_pct / atr)


def kelly_fraction(trade_r: pd.Series) -> float:
    """Full Kelly fraction from past results measured in R (profit per dollar risked at the stop).

    f* = W - (1 - W) / B, where W is the win rate and B the payoff ratio (average win divided
    by average loss). It is the share of the account to risk per trade that would grow the
    account fastest in the long run if W and B were known exactly. Zero or negative means the
    past trades show no edge (do not bet).
    """
    wins, losses = trade_r[trade_r > 0], -trade_r[trade_r < 0]
    if len(wins) == 0:
        return -1.0
    if len(losses) == 0:
        return 1.0
    w = len(wins) / len(trade_r)
    b = wins.mean() / losses.mean()
    return w - (1 - w) / b


@dataclass
class FractionalKelly(Sizer):
    """Risk `kelly_multiple` x the Kelly fraction of past trades, never above `cap_pct`.

    - `kelly_multiple`: 0.5 = "half Kelly". Past results are an estimate, and an estimate that
      is too high costs far more than one that is too low, so we bet only part of it.
    - `cap_pct`: hard ceiling on risk per trade, whatever the formula says.
    - `floor_pct`: used when the past shows no edge. Betting zero would stop trading for good
      (no new trades means no new evidence), so we keep a token size instead.
    - `min_trades`: before this many closed trades the estimate is mostly noise, so fall back
      to `FixedFractional(base_pct)`.
    """

    kelly_multiple: float = 0.5
    cap_pct: float = 0.02
    floor_pct: float = 0.0025
    min_trades: int = 30
    base_pct: float = RISK_PCT
    name = "Fractional Kelly"

    def risk_pct(self, trades: list[dict]) -> float:
        if len(trades) < self.min_trades:
            return self.base_pct
        r = pd.Series([t["net_pnl"] / t["stop_risk"] for t in trades])
        f = self.kelly_multiple * kelly_fraction(r)
        return min(max(f, self.floor_pct), self.cap_pct)

    def shares(self, *, equity, fill, risk_per_share, entry_time, trades) -> int:
        return math.floor(equity * self.risk_pct(trades) / risk_per_share)
