"""opening_range_breakout, written from strategies/specs/opening_range_breakout.yaml (version 1).

The opening range is the high and low of the first N minutes after the 09:30 open (N = 5 or
15). Buy when a 5-minute bar closes above that high: the bet is that leaving the opening
range upward starts a move that lasts into the close. Stop: the low of the opening range.
No profit target by default (optional `target_r`: sell at entry + target_r x (entry - stop)):
hold to the stop or the flatten just before the close. At most one entry a
day: only the FIRST bar that meets the entry rule counts, so after a stop-out the strategy
does not buy again. With `require_above_vwap` the close must also be above VWAP (the day's
volume-weighted average price), so the buy only happens when the breakout is also above what
the average buyer paid today. Long only. Paper trading / education only. Nothing here is
financial advice.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy
from strategies.session_clock import BAR_MINUTES, SessionClock


@dataclass(frozen=True)
class OpeningRangeBreakoutParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    range_minutes: int = 5  # length of the opening range: 5 or 15 (a multiple of the bar size)
    require_above_vwap: bool = False  # the VWAP variant sets this to True
    target_r: float | None = None  # profit target in R (multiples of entry - stop); None = no target
    open_time: time = time(9, 30)
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE

    def __post_init__(self):
        if self.range_minutes <= 0 or self.range_minutes % BAR_MINUTES:
            raise ValueError(f"range_minutes must be a multiple of {BAR_MINUTES}")
        if self.target_r is not None and self.target_r <= 0:
            raise ValueError("target_r must be positive (or None for no target)")


class OpeningRangeBreakout(Strategy):
    def __init__(self, params: OpeningRangeBreakoutParams | None = None):
        self.p = params or OpeningRangeBreakoutParams()
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

        if position is not None:
            return Exit("time") if flatten_now else None
        if not may_enter:
            return None

        # Today's bars only. The open bar starts at 09:30; the range is its first N minutes.
        local = bars.index.tz_convert(p.timezone)
        today = bars[local.date == start.date()]
        today_local = local[local.date == start.date()]
        open_at = pd.Timestamp.combine(start.date(), p.open_time).tz_localize(p.timezone)
        range_end = open_at + pd.Timedelta(minutes=p.range_minutes)
        in_range = (today_local >= open_at) & (today_local < range_end)
        if in_range.sum() < p.range_minutes // BAR_MINUTES:  # range incomplete or data gap
            return None
        after = today[today_local >= range_end]  # signal bars: the range has fully closed
        if after.empty or after.index[-1] != bars.index[-1]:
            return None  # the latest bar is still inside the range

        range_high = float(today["high"][in_range].max())
        range_low = float(today["low"][in_range].min())

        breakout = after["close"] > range_high  # strictly above: a close at the high is not a break
        if p.require_above_vwap:
            vwap = indicators.vwap(today["high"], today["low"], today["close"], today["volume"])
            breakout &= after["close"] > vwap.loc[after.index]
        # Only the first qualifying bar of the day is a buy (no re-entry after a stop-out).
        if not breakout.iloc[-1] or breakout.iloc[:-1].any():
            return None

        entry = float(after["close"].iloc[-1])
        if range_low >= entry:
            return None
        target = float("inf") if p.target_r is None else entry + p.target_r * (entry - range_low)
        return Entry(stop=range_low, target=target, reason="opening_range_breakout")
