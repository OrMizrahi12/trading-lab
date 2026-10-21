"""Offline tests for backtests/metrics.py on hand-made equity curves and trades."""

import math

import numpy as np
import pandas as pd
import pytest

from backtests.metrics import compute_metrics, daily_returns, drawdown, exposure


def curve(values_by_day, start="2026-03-02"):
    """One bar per day at 16:00 New York, so each bar is its own session's last bar."""
    idx = pd.bdate_range(start, periods=len(values_by_day)).tz_localize("America/New_York")
    idx = (idx + pd.Timedelta(hours=16)).tz_convert("UTC")
    return pd.Series(values_by_day, index=idx, dtype=float)


def trades(pnls, rs=None):
    t0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")
    return pd.DataFrame({
        "entry_time": [t0] * len(pnls),
        "exit_time": [t0 + pd.Timedelta(minutes=10)] * len(pnls),
        "net_pnl": pnls,
        "r_multiple": rs if rs is not None else [p / 100 for p in pnls],
    })


def test_daily_returns_first_day_uses_start_equity():
    r = daily_returns(curve([101, 101, 103.02]), 100)
    assert r.round(6).tolist() == [0.01, 0.0, 0.02]


def test_max_drawdown_counts_start_equity_as_a_peak():
    eq = curve([90, 95, 120, 96])
    assert drawdown(eq, 100).min() == pytest.approx(-0.2)  # 120 -> 96
    assert compute_metrics(trades([1]), curve([90, 95]), 100)["max_drawdown"] == pytest.approx(-0.1)


def test_sharpe_and_sortino_match_hand_calculation():
    eq = curve([101, 100.99, 103.0099, 102.9], 100)
    r = daily_returns(eq, 100).to_numpy()
    m = compute_metrics(trades([1]), eq, 100)
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    downside = math.sqrt((np.minimum(r, 0) ** 2).mean())
    assert m["sortino"] == pytest.approx(r.mean() / downside * math.sqrt(252))


def test_trade_stats():
    m = compute_metrics(trades([300, -100, -100, 100], [3, -1, -1, 1]), curve([100, 100]), 100)
    assert m["trades"] == 4
    assert m["net_profit"] == 0
    assert m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(2.0)
    assert m["expectancy"] == pytest.approx(50)
    assert m["avg_r"] == pytest.approx(0.5)


def test_cagr_is_annualised_growth():
    idx = pd.DatetimeIndex(["2024-01-01", "2026-01-01"], tz="UTC")  # 731 days
    eq = pd.Series([100.0, 121.0], index=idx)
    years = 731 / 365.25
    assert compute_metrics(trades([1]), eq, 100)["cagr"] == pytest.approx(1.21 ** (1 / years) - 1)


def test_exposure_counts_bars_inside_trades():
    idx = pd.date_range("2026-03-02 14:30", periods=10, freq="5min", tz="UTC")
    t = pd.DataFrame({"entry_time": [idx[2]], "exit_time": [idx[5]]})
    assert exposure(t, idx) == pytest.approx(0.3)  # bars 2, 3, 4


def test_no_trades_and_no_losses():
    m = compute_metrics(trades([]), curve([100, 100]), 100)
    assert m["trades"] == 0 and math.isnan(m["win_rate"]) and math.isnan(m["sharpe"])
    assert compute_metrics(trades([5, 5]), curve([101, 102]), 100)["profit_factor"] == math.inf
