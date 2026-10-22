"""Offline tests for the gap scanner in scanner.py: a fake IB serves canned scans and bars, no Gateway needed."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from ib_async import BarData, Contract, Event

import scanner
from market_data import Throttle

NY = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 22)


def make_bars(symbol_days, today_bars=6, today_open=None, today_volume=6000, volume=3000, price=100.0, daily_range=2.0):
    """20 earlier weekday sessions of 78 five-minute bars each, then `today_bars` bars of TODAY (UTC index).

    Every earlier session opens and closes at `price` and spans +-daily_range/2, so ATR is daily_range.
    """
    rows, day = [], TODAY - timedelta(days=1)
    days = []
    while len(days) < symbol_days:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    for d in reversed(days):
        for i in range(78):
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY) + timedelta(minutes=5 * i)
            hi, lo = (price + daily_range / 2, price) if i == 0 else (price, price - daily_range / 2) if i == 1 else (price, price)
            rows.append((t, price, hi, lo, price, volume))
    open_ = price if today_open is None else today_open
    for i in range(today_bars):
        t = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 30, tzinfo=NY) + timedelta(minutes=5 * i)
        rows.append((t, open_, open_ + 0.1, open_ - 0.1, open_, today_volume))
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")


def to_bar_data(df):
    return [
        BarData(date=t.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, average=r.close, barCount=1)
        for t, r in df.iterrows()
    ]


class FakeIB:
    def __init__(self, scans, bars, errors=None):
        self.scans, self.bars, self.errors = scans, bars, errors or {}
        self.errorEvent = Event()
        self.subscriptions, self.history_requests = [], []

    def sleep(self, seconds):
        pass

    def reqScannerData(self, sub):
        self.subscriptions.append(sub)
        return [SimpleNamespace(contractDetails=SimpleNamespace(contract=Contract(symbol=s, secType="STK"))) for s in self.scans[sub.scanCode]]

    def reqHistoricalData(self, contract, **kwargs):
        self.history_requests.append((contract.symbol, kwargs))
        if contract.symbol in self.errors:
            code, text = self.errors[contract.symbol]
            self.errorEvent.emit(1, code, text)
            return []
        return to_bar_data(self.bars.get(contract.symbol, pd.DataFrame())) if contract.symbol in self.bars else []


# --- gap_stats ---------------------------------------------------------------------------


def test_gap_numbers():
    # Earlier sessions: close 100, ATR 2 (each day spans 2, no gaps). Today opens 104.
    stats = scanner.gap_stats(make_bars(20, today_open=104.0), TODAY)
    assert stats["gap_pct"] == pytest.approx(4.0)
    assert stats["gap_atr"] == pytest.approx(2.0)  # 4 point gap / ATR 2
    assert stats["price"] == pytest.approx(104.0)
    assert stats["avg_dollar_vol"] == pytest.approx(78 * 3000 * 100.0)


def test_gap_down_is_negative():
    stats = scanner.gap_stats(make_bars(20, today_open=97.0), TODAY)
    assert stats["gap_pct"] == pytest.approx(-3.0)
    assert stats["gap_atr"] == pytest.approx(-1.5)


def test_relative_volume_compares_same_time_of_day():
    # 6 bars today, the last still forming -> 5 completed bars at 2000 = 10000; usual 5 x 1000 = 5000.
    stats = scanner.gap_stats(make_bars(20, today_bars=6, today_volume=2000, volume=1000), TODAY)
    assert stats["rel_vol"] == pytest.approx(2.0)


def test_relative_volume_not_available_with_one_bar():
    stats = scanner.gap_stats(make_bars(20, today_bars=1), TODAY)
    assert pd.isna(stats["rel_vol"])


def test_skip_reasons():
    assert scanner.gap_stats(None, TODAY) == "no bars"
    assert "no bars for" in scanner.gap_stats(make_bars(20), date(2026, 10, 23))
    assert "earlier sessions" in scanner.gap_stats(make_bars(10), TODAY)


# --- filters and ranking -----------------------------------------------------------------


def row(**kw):
    base = {"price": 50.0, "avg_dollar_vol": 50e6, "gap_pct": 3.0, "gap_atr": 0.5}
    return {**base, **kw}


def test_filters():
    assert scanner.passes_filters(row())
    assert scanner.passes_filters(row(gap_pct=-2.0, gap_atr=0.1))  # 2% is enough, either direction
    assert scanner.passes_filters(row(gap_pct=0.5, gap_atr=-1.2))  # 1 ATR is enough on its own
    assert not scanner.passes_filters(row(gap_pct=1.9, gap_atr=0.9))
    assert not scanner.passes_filters(row(price=5.0))  # must be above 5
    assert not scanner.passes_filters(row(avg_dollar_vol=19.9e6))


def test_rank_by_rel_vol_puts_missing_last():
    t = pd.DataFrame({"symbol": ["A", "B", "C"], "rel_vol": [1.5, float("nan"), 3.0]})
    assert list(scanner.rank_by_rel_vol(t)["symbol"]) == ["C", "A", "B"]


# --- full scan with a fake IB ------------------------------------------------------------


def fresh_throttle():
    return Throttle()


def test_run_gap_scan_filters_ranks_and_dedupes():
    bars = {
        "BIG": make_bars(20, today_open=104.0, today_volume=9000),  # gap 4%, rel vol 3
        "MID": make_bars(20, today_open=103.0, today_volume=6000),  # gap 3%, rel vol 2
        "TINY": make_bars(20, today_open=100.1),  # gap 0.1% = 0.05 ATR: no
        "THIN": make_bars(20, today_open=104.0, volume=1, today_volume=1),  # dollar volume far under $20M
        "CHEAP": make_bars(20, today_open=4.2, price=4.0),  # price under 5
    }
    ib = FakeIB({"HIGH_OPEN_GAP": ["MID", "BIG", "TINY", "CHEAP"], "LOW_OPEN_GAP": ["THIN", "BIG"]}, bars)
    ranked, measured, skipped = scanner.run_gap_scan(ib, TODAY, throttle=fresh_throttle())
    assert list(ranked["symbol"]) == ["BIG", "MID"]
    assert len(measured) == 5 and not skipped
    assert [s for s, _ in ib.history_requests].count("BIG") == 1  # in both scans, asked for once
    assert all(kw["useRTH"] and kw["whatToShow"] == "TRADES" for _, kw in ib.history_requests)


def test_scan_subscriptions_are_stocks_only_no_etfs():
    ib = FakeIB({"HIGH_OPEN_GAP": [], "LOW_OPEN_GAP": []}, {})
    scanner.run_gap_scan(ib, TODAY, throttle=fresh_throttle())
    assert [s.scanCode for s in ib.subscriptions] == ["HIGH_OPEN_GAP", "LOW_OPEN_GAP"]
    for s in ib.subscriptions:
        assert (s.instrument, s.stockTypeFilter, s.abovePrice, s.marketCapAbove) == ("STK", "CORP", 5.0, 300)
        assert s.locationCode.startswith("STK.US")


def test_symbol_without_bars_is_skipped_not_fatal():
    ib = FakeIB({"HIGH_OPEN_GAP": ["GONE", "OK"], "LOW_OPEN_GAP": []}, {"OK": make_bars(20, today_open=104.0)})
    ranked, _, skipped = scanner.run_gap_scan(ib, TODAY, throttle=fresh_throttle())
    assert list(ranked["symbol"]) == ["OK"]
    assert skipped == {"GONE": "no bars"}


def test_pacing_error_stops_further_requests():
    pacing = (162, "Historical Market Data Service error message: pacing violation")
    ib = FakeIB({"HIGH_OPEN_GAP": ["A", "B", "C"], "LOW_OPEN_GAP": []}, {"A": make_bars(20, today_open=104.0)}, errors={"B": pacing})
    ranked, _, skipped = scanner.run_gap_scan(ib, TODAY, throttle=fresh_throttle())
    assert list(ranked["symbol"]) == ["A"]
    assert set(skipped) == {"B", "C"}
    assert [s for s, _ in ib.history_requests] == ["A", "B"]  # C never asked


# --- saving and display ------------------------------------------------------------------


def test_save_watchlist_is_dated(tmp_path):
    table = pd.DataFrame({"symbol": ["BIG"], "rel_vol": [3.0]})
    path = scanner.save_watchlist(table, TODAY, tmp_path / "watchlists")
    assert path.name == "gap_scan_2026-10-22.csv"
    assert pd.read_csv(path)["symbol"].tolist() == ["BIG"]


def test_format_table_handles_empty_and_missing_rel_vol():
    assert "no stocks" in scanner.format_table(pd.DataFrame())
    t = pd.DataFrame([{"symbol": "X", "scan": "HIGH_OPEN_GAP", "gap_pct": 3.0, "gap_atr": 1.5, "rel_vol": float("nan"), "price": 10.0, "avg_dollar_vol": 25e6}])
    text = scanner.format_table(t)
    assert "n/a" in text and "+3.00" in text and "25" in text


def test_scanner_cancel_notice_with_code_162_is_not_pacing():
    notice = (162, "Historical Market Data Service error message:API scanner subscription cancelled: 40")
    ib = FakeIB({"HIGH_OPEN_GAP": ["A", "B"], "LOW_OPEN_GAP": []}, {"A": make_bars(20, today_open=104.0), "B": make_bars(20, today_open=103.0)}, errors={"A": notice})
    # A's request sees the stray notice and gets no bars (skipped on its own), but B is still asked for.
    ranked, _, skipped = scanner.run_gap_scan(ib, TODAY, throttle=fresh_throttle())
    assert [s for s, _ in ib.history_requests] == ["A", "B"]
    assert list(ranked["symbol"]) == ["B"]
    assert "pacing" not in skipped["A"]
