"""bollinger_rsi_reversion, written from strategies/specs/bollinger_rsi_reversion.yaml (version 1).

Mean reversion (the bet that a stretched price snaps back toward its average). Buy when the
close is below the lower Bollinger band (20-bar average minus 2 standard deviations, a
measure of how spread out prices are) and the 2-bar RSI (relative strength index, here over
just 2 bars, so it swings fast) is under 10, meaning the last couple of bars were almost all
selling. Exit when a bar closes at or above the middle band (the 20-bar average). Stop: 2 ATR
(average bar size) below the entry. Flatten before the close. Paper trading / education only.
Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import SessionClock, entry_blocked, trend_day_blocked


@dataclass(frozen=True)
class BollingerRsiReversionParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    bb_period: int = 20
    bb_k: float = 2.0
    rsi_period: int = 2
    rsi_max: float = 10.0
    atr_period: int = 14
    atr_multiple: float = 2.0
    first_signal: time = time(9, 45)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    no_entry_from: time | None = None  # e.g. time(11, 30): midday filter, off by default
    no_entry_until: time | None = None  # e.g. time(14, 0)
    max_open_distance_atr: float | None = None  # trend-day filter: no entry once the close is further than this many ATRs from today's open; off by default
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE


class BollingerRsiReversion(Strategy):
    def __init__(self, params: BollingerRsiReversionParams | None = None):
        self.p = params or BollingerRsiReversionParams()
        self._clock = SessionClock(self.p.calendar)

    def on_bar(self, bars: pd.DataFrame, position: Position | None) -> Entry | Exit | None:
        p = self.p
        start = bars.index[-1].tz_convert(p.timezone)
        phase = self._clock.phase(
            start, p.last_entry_minutes_before_close, p.flatten_minutes_before_close
        )
        if phase is None:  # data on a day the exchange was shut: do nothing
            return Exit("time") if position is not None else None
        flatten_now, may_enter = phase

        if position is not None and flatten_now:
            return Exit("time")
        if position is None and (
            not may_enter
            or start.time() < p.first_signal
            or entry_blocked(start, p.no_entry_from, p.no_entry_until)
            or trend_day_blocked(bars, p.timezone, p.max_open_distance_atr, p.atr_period)
        ):
            return None
        if len(bars) <= max(p.bb_period, p.atr_period, p.rsi_period):
            return None

        close = bars["close"]
        bands = indicators.bollinger(close, p.bb_period, p.bb_k)
        last = float(close.iloc[-1])

        if position is not None:  # NaN compares False, so no exit on an undefined band
            return Exit("middle_band") if last >= bands["middle"].iloc[-1] else None

        if not last < bands["lower"].iloc[-1]:
            return None
        rsi = indicators.rsi(close, p.rsi_period).iloc[-1]
        if not rsi < p.rsi_max:
            return None
        atr = indicators.atr(bars["high"], bars["low"], close, p.atr_period).iloc[-1]
        if pd.isna(atr):
            return None
        stop = last - p.atr_multiple * float(atr)
        if stop >= last:
            return None
        return Entry(stop=stop, target=float("inf"), reason="bollinger_rsi_reversion")
