"""Performance metrics for a backtest: `compute_metrics(trades, equity, start_equity)`.

Takes the `trades` table and the per-bar `equity` curve from
`backtests.engine.BacktestResult`. Daily returns are the percent change of the
equity at each New York session's last bar, the first day measured from the
starting equity. Sharpe and Sortino use a 0% risk-free rate and 252 trading days
a year. `METRICS` lists every number with a label, a display format and a
one-sentence plain-English explanation, in the order the report shows them.

Paper research only. Nothing here is financial advice.
"""

from typing import NamedTuple

import numpy as np
import pandas as pd

import indicators

TRADING_DAYS = 252


class Metric(NamedTuple):
    key: str
    label: str
    fmt: str  # format spec for the value; "pct" values are stored as fractions
    explanation: str


METRICS = [
    Metric("net_profit", "Net profit", "$,.0f",
           "Dollars gained or lost over the whole test, after all costs."),
    Metric("cagr", "CAGR", "pct",
           "Compound annual growth rate: the steady yearly % return that would turn the "
           "starting money into the ending money."),
    Metric("sharpe", "Sharpe ratio", ".2f",
           "Average daily return divided by how much daily returns bounce around, scaled to a "
           "year; higher means smoother gains, and above 1 is generally considered decent."),
    Metric("sortino", "Sortino ratio", ".2f",
           "Like Sharpe, but only down days count as risk, so big up days are not punished."),
    Metric("max_drawdown", "Max drawdown", "pct",
           "The worst fall from a previous equity peak to a later low, i.e. the most "
           "you were ever down from your best."),
    Metric("profit_factor", "Profit factor", ".2f",
           "Total dollars won divided by total dollars lost; above 1 means winners outweigh losers."),
    Metric("expectancy", "Expectancy", "$,.2f",
           "The average net dollars you make (or lose) per trade."),
    Metric("win_rate", "Win rate", "pct",
           "The share of trades that finished with a profit after costs."),
    Metric("avg_r", "Average R", ".2f",
           "The average trade result in R, where 1R is the amount risked if the stop is hit "
           "(1% of equity here), so 0.2R means a 0.2% gain on the account per trade."),
    Metric("exposure", "Exposure", "pct",
           "The share of bars during which a position was open; the rest of the time you held cash."),
    Metric("trades", "Trades", ",d", "How many round trips (a buy followed by its sell) were made."),
]


def daily_returns(equity: pd.Series, start_equity: float) -> pd.Series:
    """Percent change of the end-of-session equity, one value per New York trading day."""
    day = equity.index.tz_convert(indicators.SESSION_TZ).date
    closes = equity.groupby(day).last()
    prev = closes.shift(1)
    prev.iloc[0] = start_equity
    return closes / prev - 1


def drawdown(equity: pd.Series, start_equity: float) -> pd.Series:
    """Fractional distance below the running peak (0 at a peak, -0.1 = 10% under it)."""
    peak = equity.cummax().clip(lower=start_equity)
    return equity / peak - 1


def exposure(trades: pd.DataFrame, index: pd.DatetimeIndex) -> float:
    """Share of bars in `index` that fall inside a trade (entry bar included, exit bar not)."""
    if trades.empty or len(index) == 0:
        return 0.0
    first = index.searchsorted(trades["entry_time"], side="left")
    last = index.searchsorted(trades["exit_time"], side="left")
    return float((last - first).sum() / len(index))


def compute_metrics(trades: pd.DataFrame, equity: pd.Series, start_equity: float) -> dict[str, float]:
    nan = float("nan")
    out = dict.fromkeys((m.key for m in METRICS), nan)
    out["trades"] = len(trades)
    if equity.empty:
        return out

    end = float(equity.iloc[-1])
    out["net_profit"] = end - start_equity
    out["max_drawdown"] = float(drawdown(equity, start_equity).min())

    years = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400)
    if years > 0:
        out["cagr"] = (end / start_equity) ** (1 / years) - 1 if end > 0 else -1.0

    r = daily_returns(equity, start_equity)
    if len(r) > 1:
        std = r.std(ddof=1)
        if std > 0:
            out["sharpe"] = r.mean() / std * np.sqrt(TRADING_DAYS)
        downside = np.sqrt((np.minimum(r, 0.0) ** 2).mean())
        if downside > 0:
            out["sortino"] = r.mean() / downside * np.sqrt(TRADING_DAYS)

    if not trades.empty:
        net = trades["net_pnl"]
        won, lost = net[net > 0].sum(), -net[net < 0].sum()
        out["profit_factor"] = won / lost if lost > 0 else float("inf")
        out["expectancy"] = float(net.mean())
        out["win_rate"] = float((net > 0).mean())
        out["avg_r"] = float(trades["r_multiple"].mean())
        out["exposure"] = exposure(trades, equity.index)
    return out


def format_value(metric: Metric, value: float) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if np.isinf(value):
        return "∞"
    if metric.fmt == "pct":
        return f"{value:.1%}"
    if metric.fmt.startswith("$"):
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):{metric.fmt[1:]}}"
    return f"{value:{metric.fmt}}"
