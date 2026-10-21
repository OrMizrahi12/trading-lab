"""spy_vwap_reclaim, written from strategies/specs/spy_vwap_reclaim.yaml (version 1).

Buy when the price dips under VWAP (the day's volume-weighted average price) and
then closes back above it, while the 20-bar EMA (a fast-reacting average) is still
above VWAP. Stop: just under the recent swing low (lowest low of the last 10
bars), but never more than 1.5 ATR (average bar size) below the entry. Target: 2R,
where R is the dollars risked per share. Flatten before the close. Paper trading /
education only. Nothing here is financial advice.
"""

from dataclasses import dataclass
from datetime import time

import exchange_calendars as xcals
import pandas as pd

import indicators
from strategies.base import Entry, Exit, Position, Strategy

BAR_MINUTES = 5


@dataclass(frozen=True)
class SpyVwapReclaimParams:
    """Defaults mirror the YAML spec; change them here, not in the logic."""

    ema_period: int = 20
    atr_period: int = 14
    atr_multiple: float = 1.5
    swing_lookback: int = 10
    swing_buffer: float = 0.01
    r_multiple: float = 2.0
    first_signal: time = time(9, 45)  # session start 09:30 + skip_first_minutes 15
    # Measured from each day's real NYSE close (13:00 on a half day, not a fixed 16:00).
    last_entry_minutes_before_close: int = 30
    flatten_minutes_before_close: int = 5
    max_trades_per_day: int = 1
    timezone: str = indicators.SESSION_TZ
    calendar: str = "XNYS"  # NYSE


class SpyVwapReclaim(Strategy):
    def __init__(self, params: SpyVwapReclaimParams | None = None):
        self.p = params or SpyVwapReclaimParams()
        self._cal = xcals.get_calendar(self.p.calendar)
        self._closes: dict = {}  # session date -> real close (UTC), or None if not a session
        self.reset()

    def _session_close(self, day) -> pd.Timestamp | None:
        if day not in self._closes:
            try:
                self._closes[day] = self._cal.session_close(pd.Timestamp(day))
            except xcals.errors.NotSessionError:
                self._closes[day] = None
        return self._closes[day]

    def reset(self) -> None:
        """Forget the per-day trade count. Call between independent runs."""
        self._entries: dict = {}  # session date -> entries signalled that day

    def on_bar(self, bars: pd.DataFrame, position: Position | None) -> Entry | Exit | None:
        p = self.p
        local = bars.index.tz_convert(p.timezone)
        start = local[-1]
        day = start.date()
        session_close = self._session_close(day)
        if session_close is None:  # data on a day the exchange was shut: do nothing
            return Exit("time") if position is not None else None
        # The decision is filled at the NEXT open, so to sell at the flatten time we
        # must decide on the bar that closes then (the one starting 5 minutes earlier).
        bar_end = start + pd.Timedelta(minutes=BAR_MINUTES)
        flatten_at = session_close - pd.Timedelta(minutes=p.flatten_minutes_before_close)
        last_entry = session_close - pd.Timedelta(minutes=p.last_entry_minutes_before_close)

        if position is not None:
            return Exit("time") if bar_end >= flatten_at else None

        if not (p.first_signal <= start.time() and start < last_entry):
            return None
        if len(bars) < 2 or self._entries.get(day, 0) >= p.max_trades_per_day:
            return None

        close, high, low = bars["close"], bars["high"], bars["low"]
        vwap = indicators.vwap(high, low, close, bars["volume"])
        ema = indicators.ema(close, p.ema_period) if len(bars) >= p.ema_period else None
        atr = indicators.atr(high, low, close, p.atr_period) if len(bars) > p.atr_period else None
        if ema is None or atr is None:
            return None
        v, e, a = vwap.iloc[-1], ema.iloc[-1], atr.iloc[-1]
        if pd.isna(v) or pd.isna(e) or pd.isna(a) or pd.isna(vwap.iloc[-2]):
            return None

        if not (e > v and close.iloc[-2] <= vwap.iloc[-2] and close.iloc[-1] > v):
            return None

        # Swing low from today's bars only, so a gap day ignores yesterday's lows.
        today = low[local.date == day].iloc[-p.swing_lookback :]
        entry = float(close.iloc[-1])
        stop = max(today.min() - p.swing_buffer, entry - p.atr_multiple * a)
        if stop >= entry or low.iloc[-1] < stop:
            return None
        self._entries[day] = self._entries.get(day, 0) + 1
        return Entry(stop=float(stop), target=entry + p.r_multiple * (entry - stop),
                     reason="vwap_reclaim")
