"""Cross-check of backtests/engine.py against the independent library backtesting.py.

The strategy rules are re-implemented here from strategies/specs/spy_vwap_reclaim.yaml
(not by calling SpyVwapReclaim), with EMA and ATR from TA-Lib instead of indicators.py,
so a bug in either the lab's indicators, strategy or engine shows up as a difference.

Two runs, both over the whole SPY 5min cache:
  - "no costs": every cost is zero, to compare the trading logic alone;
  - "costs":    commission 1c, half of a 1c spread (0.5c) and 1c slippage per share per
                side, as in backtests.engine.CostModel.

Run: uv run -m backtests.crosscheck_backtestingpy

Paper research only. Nothing here touches a broker. Nothing here is financial advice.
"""

import math
import warnings

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import talib
from backtesting import Backtest, Strategy

from backtests.engine import CostModel, RISK_PCT, STARTING_EQUITY, run_backtest
from backtests.vectorized import CACHE, load_bars
from strategies.spy_vwap_reclaim import SpyVwapReclaim

NY = "America/New_York"
COST_PER_SIDE = 0.01 + 0.005 + 0.01  # commission + half spread + slippage, $ per share


def precompute(bars: pd.DataFrame) -> pd.DataFrame:
    """Everything the rules need, computed once over the full history (no look-ahead)."""
    h, l, c, v = (bars[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
    local = bars.index.tz_convert(NY)
    day = pd.Series(local.date, index=bars.index)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap = (typical * bars["volume"]).groupby(day).cumsum() / bars["volume"].groupby(day).cumsum()
    # lowest low of today's last 10 bars, using today's bars only
    swing = bars["low"].groupby(day).transform(lambda s: s.rolling(10, min_periods=1).min())
    cal = xcals.get_calendar("XNYS")
    closes = cal.schedule["close"].dt.tz_convert(NY)
    close_of_day = pd.Series([closes.get(pd.Timestamp(d)) for d in day], index=bars.index)
    out = pd.DataFrame(
        {"vwap": vwap, "ema": talib.EMA(c, 20), "atr": talib.ATR(h, l, c, 14), "swing": swing,
         "start": local, "day": day, "sess_close": close_of_day}
    )
    return out


def make_strategy(sig: pd.DataFrame, raw_open: np.ndarray, costs_on: bool, live_target: dict):
    cs = COST_PER_SIDE if costs_on else 0.0
    start, sess_close, day = sig["start"].to_numpy(), sig["sess_close"], sig["day"].to_numpy()
    ema, vwap, atr, swing = (sig[k].to_numpy() for k in ("ema", "vwap", "atr", "swing"))
    five = pd.Timedelta(minutes=5)

    class VwapReclaim(Strategy):
        def init(self):
            self.signals_per_day: dict = {}
            self.skipped: list = []  # entries we did not place, with the reason

        def next(self):
            i = len(self.data) - 1
            t = pd.Timestamp(start[i])
            close = self.data.Close
            flatten_at = sess_close.iloc[i] - pd.Timedelta(minutes=5)
            last_entry = sess_close.iloc[i] - pd.Timedelta(minutes=30)
            if self.position:
                if t + five >= flatten_at:
                    self.position.close()  # filled at the next open
                return
            if i + 1 >= len(raw_open):
                return
            if not (t.time() >= pd.Timestamp("09:45").time() and t < last_entry):
                return
            if self.signals_per_day.get(day[i], 0) >= 1 or i < 1:
                return
            e, v, a = ema[i], vwap[i], atr[i]
            if np.isnan([e, v, a, vwap[i - 1]]).any():
                return
            if not (e > v and close[-2] <= vwap[i - 1] and close[-1] > v):
                return
            entry = close[-1]
            stop = max(swing[i] - 0.01, entry - 1.5 * a)
            if stop >= entry or self.data.Low[-1] < stop:
                return
            self.signals_per_day[day[i]] = 1
            target = entry + 2.0 * (entry - stop)
            # Size like the engine does: from the actual next-open fill. This peeks at the
            # next open only to copy the engine's sizing rule; it does not affect the signal.
            nxt = raw_open[i + 1]
            fill = nxt + 0.015 * costs_on  # half spread + slippage; commission is added below
            if stop >= fill:
                self.skipped.append((t, "stop above fill"))
                return
            risk_ps = fill - stop + 2 * 0.01 * costs_on + (0.005 + 0.01) * costs_on
            qty = math.floor(self.equity * RISK_PCT / risk_ps)
            qty = min(qty, math.floor(self.equity / fill))
            if qty > 0:
                live_target["tp"] = target
                self.buy(size=qty, sl=stop, tp=target)

    return VwapReclaim


def run_bt(bars: pd.DataFrame, sig: pd.DataFrame, costs_on: bool):
    ohlcv = bars.rename(columns=str.capitalize)[["Open", "High", "Low", "Close", "Volume"]].copy()
    ohlcv.index = ohlcv.index.tz_convert(NY).tz_localize(None)
    live_target = {"tp": float("inf")}
    strat = make_strategy(sig, bars["open"].to_numpy(float), costs_on, live_target)

    def commission(size, price):
        # backtesting.py has one cost number per fill and applies the spread to entries only.
        # The engine's target exit is a limit order: it pays no slippage (1c less). A target
        # fill is at or above the target price, a stop or time exit is below it.
        return abs(size) * (COST_PER_SIDE - 0.01 if price >= live_target["tp"] else COST_PER_SIDE)

    if not costs_on:
        commission = 0.0
    bt = Backtest(ohlcv, strat, cash=STARTING_EQUITY, commission=commission, margin=1.0,
                  trade_on_close=False, exclusive_orders=False, finalize_trades=True)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        stats = bt.run()
    return stats, [str(x.message)[:120] for x in w]


def tidy_bt(stats) -> pd.DataFrame:
    t = stats["_trades"].copy()
    reason = np.where(t["ExitPrice"] <= t["SL"], "stop",
                      np.where(t["ExitPrice"] >= t["TP"], "target", "time"))
    return pd.DataFrame({"entry_time": t["EntryTime"], "exit_time": t["ExitTime"],
                         "shares": t["Size"], "entry": t["EntryPrice"], "exit": t["ExitPrice"],
                         "reason": reason, "net_pnl": t["PnL"]})


def tidy_engine(trades: pd.DataFrame, costs_on: bool) -> pd.DataFrame:
    buy_cost = 0.015 if costs_on else 0.0
    sell_cost = np.where(trades["reason"] == "target", 0.005, 0.015) if costs_on else 0.0
    return pd.DataFrame({
        "entry_time": trades["entry_time"].dt.tz_convert(NY).dt.tz_localize(None),
        "exit_time": trades["exit_time"].dt.tz_convert(NY).dt.tz_localize(None),
        "shares": trades["shares"], "entry": trades["entry_price"] - buy_cost,
        "exit": trades["exit_price"] + sell_cost, "reason": trades["reason"],
        "net_pnl": trades["net_pnl"]})


def main() -> None:
    bars = load_bars(CACHE)
    sig = precompute(bars)
    print(f"SPY 5min {bars.index[0]:%Y-%m-%d} to {bars.index[-1]:%Y-%m-%d}, {len(bars)} bars\n")
    pd.set_option("display.width", 220)
    for costs_on in (False, True):
        label = "COSTS" if costs_on else "NO COSTS"
        cm = CostModel() if costs_on else CostModel(0.0, 0.0, 0.0)
        eng = run_backtest(bars, SpyVwapReclaim(), costs=cm)
        stats, warns = run_bt(bars, sig, costs_on)
        e, b = tidy_engine(eng.trades, costs_on), tidy_bt(stats)
        print(f"===== {label} =====")
        print(f"{'':>14}{'engine':>14}{'backtesting.py':>16}")
        print(f"{'trades':>14}{len(e):>14}{len(b):>16}")
        print(f"{'total return %':>14}{eng.stats['total_return_%']:>14.3f}{stats['Return [%]']:>16.3f}")
        print(f"{'win rate %':>14}{eng.stats['win_rate_%']:>14.2f}{stats['Win Rate [%]']:>16.2f}")
        print(f"{'net P&L $':>14}{e.net_pnl.sum():>14.2f}{b.net_pnl.sum():>16.2f}")
        print("warnings from backtesting.py:", sorted(set(warns)) or "none")
        m = e.merge(b, on="entry_time", how="outer", suffixes=("_eng", "_bt"), indicator=True)
        print("\nmatch by entry time:", m["_merge"].value_counts().to_dict())
        print("\nfirst 10 trades (eng vs bt):")
        print(m.head(10).drop(columns="_merge").to_string(float_format="%.3f"))
        both = m[m["_merge"] == "both"]
        diff = both[(both.shares_eng != both.shares_bt) | (both.reason_eng != both.reason_bt)
                    | ((both.net_pnl_eng - both.net_pnl_bt).abs() > 0.01)
                    | (both.exit_time_eng != both.exit_time_bt)]
        print(f"\nmatched trades that differ: {len(diff)} of {len(both)}")
        print(diff.head(25).to_string(float_format="%.3f"))
        print("\nunmatched:")
        print(m[m["_merge"] != "both"].to_string(float_format="%.3f"))
        print()


if __name__ == "__main__":
    main()
