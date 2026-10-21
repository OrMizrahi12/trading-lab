"""Technical indicators written from scratch with pandas.

An indicator is a number computed from past prices that summarises something
(trend, momentum, volatility). Every function here is causal: the value on a
row uses only that row and earlier rows, never later ones (no look-ahead), so a
backtest using them cannot "peek" at the future. Early rows without enough
history are NaN (not-a-number, meaning "not defined yet").

Conventions match TA-Lib (the standard C indicator library) so the tests can
compare against it. Paper trading / education only. Nothing here is financial
advice.
"""

import pandas as pd

SESSION_TZ = "America/New_York"  # US stocks trade 9:30-16:00 in this time zone


def _wilder(values: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing: a running average that moves 1/n of the way to each new value.

    Seeded with the plain average of the first n values (the first output is on
    row n-1), then avg = avg + (value - avg) / n. It is an EMA with alpha = 1/n,
    so it reacts more slowly than the usual EMA of the same n.
    """
    seeded = values.copy()
    seeded.iloc[: n - 1] = float("nan")
    seeded.iloc[n - 1] = values.iloc[:n].mean()
    return seeded.ewm(alpha=1 / n, adjust=False).mean()


def sma(close: pd.Series, n: int) -> pd.Series:
    """Simple moving average: the plain mean of the last n closes."""
    return close.rolling(n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """Exponential moving average: like SMA but recent closes count more.

    Each new value moves the average 2/(n+1) of the way toward the close.
    Seeded with the SMA of the first n closes, as TA-Lib does.
    """
    seeded = close.copy()
    seeded.iloc[: n - 1] = float("nan")
    seeded.iloc[n - 1] = close.iloc[:n].mean()
    return seeded.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index, 0-100: how strong recent gains are versus losses.

    Above ~70 is often read as "overbought" (rose a lot), below ~30 "oversold".
    Average gain and average loss use Wilder's smoothing. The first value is on
    row n, because n price changes need n+1 closes.
    """
    change = close.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    # Row 0 has no change, so smooth from row 1 and put the result back on the index.
    avg_gain = _wilder(gain.iloc[1:], n).reindex(close.index)
    avg_loss = _wilder(loss.iloc[1:], n).reindex(close.index)
    total = avg_gain + avg_loss
    # A perfectly flat window (no gains, no losses) gives 0, same as TA-Lib.
    return (100 * avg_gain / total).where(total != 0, 0.0).where(total.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range: the bar's full price travel, including a gap from the prior close.

    The largest of (high - low), |high - previous close|, |low - previous close|.
    Row 0 has no previous close, so it is NaN.
    """
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1, skipna=False)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average True Range: typical bar size in price units, a volatility gauge.

    Handy for stop distances (e.g. stop = entry - 2 * ATR). Wilder-smoothed true
    range; the first value is on row n.
    """
    tr = true_range(high, low, close)
    return _wilder(tr.iloc[1:], n).reindex(close.index)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: an SMA with a band k standard deviations above and below.

    Standard deviation measures how spread out prices are, so the bands widen
    when the market is volatile. Uses the population standard deviation
    (divides by n), as TA-Lib does. Columns: lower, middle, upper.
    """
    middle = close.rolling(n).mean()
    spread = k * close.rolling(n).std(ddof=0)
    return pd.DataFrame({"lower": middle - spread, "middle": middle, "upper": middle + spread})


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume-weighted average price, restarting at every trading session.

    The average price paid so far today, where busy bars count more. Each bar is
    priced at its typical price (high + low + close) / 3. The running totals reset
    at each new New York calendar day, so yesterday never leaks into today. The
    index must be a time zone-aware DatetimeIndex (the cache is UTC).
    """
    session = close.index.tz_convert(SESSION_TZ).date
    typical = (high + low + close) / 3
    cum_value = (typical * volume).groupby(session).cumsum()
    cum_volume = volume.groupby(session).cumsum()
    return cum_value / cum_volume.where(cum_volume != 0)
