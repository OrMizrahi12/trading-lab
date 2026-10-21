"""Offline tests for indicators.py: compare with TA-Lib on cached SPY bars."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import talib

import indicators

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "SPY_5min.parquet"


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    if not CACHE.exists():
        pytest.skip("SPY_5min cache missing: run `uv run -m data_pipeline`")
    return pd.read_parquet(CACHE)


def check(ours, theirs) -> None:
    """Same values to float precision, and NaN on exactly the same rows."""
    np.testing.assert_allclose(np.asarray(ours), theirs, rtol=1e-9, atol=1e-9, equal_nan=True)


@pytest.mark.parametrize("n", [5, 20, 50])
def test_sma_matches_talib(bars, n):
    check(indicators.sma(bars.close, n), talib.SMA(bars.close.values, n))


@pytest.mark.parametrize("n", [5, 20, 50])
def test_ema_matches_talib(bars, n):
    check(indicators.ema(bars.close, n), talib.EMA(bars.close.values, n))


@pytest.mark.parametrize("n", [2, 14, 30])
def test_rsi_matches_talib(bars, n):
    check(indicators.rsi(bars.close, n), talib.RSI(bars.close.values, n))


@pytest.mark.parametrize("n", [2, 14, 30])
def test_atr_matches_talib(bars, n):
    ours = indicators.atr(bars.high, bars.low, bars.close, n)
    check(ours, talib.ATR(bars.high.values, bars.low.values, bars.close.values, n))


@pytest.mark.parametrize("n,k", [(20, 2.0), (10, 1.5)])
def test_bollinger_matches_talib(bars, n, k):
    upper, middle, lower = talib.BBANDS(bars.close.values, n, k, k)
    ours = indicators.bollinger(bars.close, n, k)
    check(ours.upper, upper)
    check(ours.middle, middle)
    check(ours.lower, lower)


def test_rsi_flat_prices_is_zero_like_talib():
    flat = pd.Series([10.0] * 30)
    check(indicators.rsi(flat, 14), talib.RSI(flat.values, 14))


def test_vwap_by_hand_resets_each_session():
    # 9:30 New York on 2026-10-20 and 2026-10-21 is 13:30 UTC (daylight time, UTC-4).
    index = pd.DatetimeIndex(
        [
            "2026-10-20 13:30", "2026-10-20 13:35", "2026-10-20 13:40",
            "2026-10-21 13:30", "2026-10-21 13:35",
        ],
        tz="UTC",
    )
    high = pd.Series([11, 12, 13, 21, 23], index=index, dtype=float)
    low = pd.Series([9, 10, 11, 19, 21], index=index, dtype=float)
    close = pd.Series([10, 11, 12, 20, 22], index=index, dtype=float)
    volume = pd.Series([100, 300, 100, 200, 200], index=index, dtype=float)
    # Typical prices are 10, 11, 12 on day 1 and 20, 22 on day 2.
    expected = [
        10.0,                                  # 1000 / 100
        (1000 + 3300) / 400,                   # 10.75
        (1000 + 3300 + 1200) / 500,            # 11.0
        20.0,                                  # day 2 starts fresh: 4000 / 200
        (4000 + 4400) / 400,                   # 21.0
    ]
    got = indicators.vwap(high, low, close, volume)
    np.testing.assert_allclose(got.values, expected)


def test_vwap_zero_volume_is_nan_not_inf():
    index = pd.DatetimeIndex(["2026-10-20 13:30", "2026-10-20 13:35"], tz="UTC")
    price = pd.Series([10.0, 10.0], index=index)
    got = indicators.vwap(price, price, price, pd.Series([0.0, 50.0], index=index))
    assert np.isnan(got.iloc[0])
    assert got.iloc[1] == 10.0


def test_vwap_on_spy_stays_inside_each_sessions_range(bars):
    got = indicators.vwap(bars.high, bars.low, bars.close, bars.volume)
    session = bars.index.tz_convert(indicators.SESSION_TZ).date
    assert not got.isna().any()
    assert (got >= bars.low.groupby(session).cummin() - 1e-9).all()
    assert (got <= bars.high.groupby(session).cummax() + 1e-9).all()


@pytest.mark.parametrize("cut", [300, 901])
def test_no_look_ahead(bars, cut):
    """Values on the first `cut` rows must not change when later rows are removed."""
    cases = {
        "sma": lambda d: indicators.sma(d.close, 20),
        "ema": lambda d: indicators.ema(d.close, 20),
        "rsi": lambda d: indicators.rsi(d.close, 14),
        "atr": lambda d: indicators.atr(d.high, d.low, d.close, 14),
        "bb": lambda d: indicators.bollinger(d.close, 20, 2.0),
        "vwap": lambda d: indicators.vwap(d.high, d.low, d.close, d.volume),
    }
    for name, fn in cases.items():
        full = pd.DataFrame(fn(bars)).iloc[:cut]
        short = pd.DataFrame(fn(bars.iloc[:cut]))
        pd.testing.assert_frame_equal(full, short, obj=name)
