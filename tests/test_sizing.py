"""Offline tests for backtests/sizing.py: each sizer on hand-made numbers, plus engine wiring."""

import pandas as pd
import pytest

from backtests.engine import CostModel, run_backtest
from backtests.sizing import FixedFractional, FractionalKelly, VolatilityTarget, kelly_fraction
from strategies.base import Entry
from tests.test_engine import FLAT, Scripted, make_bars

ZERO = CostModel(0.0, 0.0, 0.0)
T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")


def past(wins, losses, win_r=2.0):
    """`wins` winning trades of +win_r and `losses` losing trades of -1R, as engine trade dicts."""
    return ([{"net_pnl": win_r * 100.0, "stop_risk": 100.0}] * wins
            + [{"net_pnl": -100.0, "stop_risk": 100.0}] * losses)


def test_fixed_fractional_risks_one_percent():
    n = FixedFractional(0.01).shares(equity=100_000, fill=100, risk_per_share=2.0,
                                     entry_time=T0, trades=[])
    assert n == 500  # $1,000 / $2 per share


def test_kelly_fraction_known_values():
    # 50% wins paying 2R, 50% losses paying -1R: f = 0.5 - 0.5 / 2 = 0.25
    assert kelly_fraction(pd.Series([2.0, -1.0] * 10)) == pytest.approx(0.25)
    # 40% wins of 1R: f = 0.4 - 0.6 / 1 = -0.2 (no edge)
    assert kelly_fraction(pd.Series([1.0] * 4 + [-1.0] * 6)) == pytest.approx(-0.2)


def test_kelly_uses_fallback_before_min_trades():
    k = FractionalKelly(min_trades=30)
    assert k.risk_pct(past(5, 5)) == k.base_pct


def test_kelly_is_scaled_then_capped():
    # 50 trades, 50% wins at 2R: full Kelly 25%, half Kelly 12.5%, capped at 2%
    assert FractionalKelly().risk_pct(past(25, 25)) == pytest.approx(0.02)
    # a mild edge: 40% wins at 2R: f = 0.4 - 0.6/2 = 0.1, half = 5% -> still capped; use a
    # tiny edge instead: 35 wins of 1.2R / 65 losses is negative -> floor
    assert FractionalKelly().risk_pct(past(35, 65, win_r=1.2)) == pytest.approx(0.0025)
    # a small positive edge below the cap: 50% wins at 1.1R, f = 0.5-0.5/1.1 = 0.04545, half = 0.0227
    # lower multiple so it lands under the cap
    k = FractionalKelly(kelly_multiple=0.25)
    assert k.risk_pct(past(25, 25, win_r=1.1)) == pytest.approx(0.25 * (0.5 - 0.5 / 1.1))


def test_kelly_sees_only_closed_trades():
    # Engine passes its own list of closed trades: the first trade of a run sees none.
    seen = []

    class Spy(FixedFractional):
        def shares(self, **kw):
            seen.append(len(kw["trades"]))
            return super().shares(**kw)

    rows = [FLAT] * 3 + [(99.5, 99.6, 98.8, 99)] + [FLAT] * 3
    run_backtest(make_bars(rows), Scripted({1: Entry(stop=99, target=110),
                                            5: Entry(stop=95, target=110)}),
                 costs=ZERO, lookback_bars=None, sizer=Spy())
    assert seen == [0, 1]


def test_volatility_target_uses_only_earlier_sessions():
    # Ten sessions, each with a 4.0 high-low range and no gaps: daily ATR is 4.0 from session 3
    # (atr_days=2), known only the NEXT day. Session 3's own huge range must not leak in.
    days = pd.bdate_range("2026-03-02", periods=4)
    bars = pd.concat([
        make_bars([(100, 102, 98, 100)] * 3, day=str(d.date())) for d in days[:3]
    ] + [make_bars([(100, 140, 60, 100)] * 3, day=str(days[3].date()))])
    v = VolatilityTarget(risk_pct=0.01, atr_days=2)
    v.prepare(bars)
    t = pd.Timestamp(f"{days[3].date()} 09:45", tz="America/New_York")
    n = v.shares(equity=100_000, fill=100, risk_per_share=1.0, entry_time=t, trades=[])
    assert n == 250  # $1,000 / ATR 4.0, not the 80-point range of that same day


def test_volatility_target_falls_back_to_fixed_during_warmup():
    bars = make_bars([FLAT] * 4)
    v = VolatilityTarget(atr_days=14)
    v.prepare(bars)
    assert v.shares(equity=100_000, fill=100, risk_per_share=2.0,
                    entry_time=bars.index[2], trades=[]) == 500


def test_engine_applies_no_leverage_cap_to_every_sizer():
    # Volatility sizing with a tiny ATR would want far more than the account can buy.
    class Huge(FixedFractional):
        def shares(self, **kw):
            return 10**9

    rows = [FLAT] * 4
    t = run_backtest(make_bars(rows), Scripted({1: Entry(stop=99, target=110)}), costs=ZERO,
                     lookback_bars=None, sizer=Huge()).trades.iloc[0]
    assert t["shares"] == 1000  # 100k / $100


def test_default_sizer_matches_explicit_fixed_fractional():
    rows = [FLAT, FLAT, FLAT, (99.5, 99.6, 98.8, 99)]
    kw = dict(costs=CostModel(), lookback_bars=None)
    a = run_backtest(make_bars(rows), Scripted({1: Entry(stop=99, target=110)}), **kw)
    b = run_backtest(make_bars(rows), Scripted({1: Entry(stop=99, target=110)}),
                     sizer=FixedFractional(0.01), **kw)
    pd.testing.assert_frame_equal(a.trades, b.trades)
