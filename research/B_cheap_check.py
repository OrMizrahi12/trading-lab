"""Card B cheap check: vectorised long-only noise-area breakout on the research cache (no holdout).

Band = max(open, prior close) * (1 + sigma); sigma = mean of |close/open - 1| at the same time of day over the
PRIOR 14 sessions. Enter at the next open after a 30-minute-mark close above the band and above VWAP (10:00-15:00),
exit at the next open after a close back under the band or VWAP, else at the close. Costs: 5 cents a share round trip.
Run: uv run python research/B_cheap_check.py
"""
import numpy as np
import pandas as pd

COST = 0.05  # commission 2x0.01 + spread 0.01 + slippage 2x0.01, dollars per share
for sym in ("SPY", "QQQ"):
    df = pd.read_parquet(f"data/cache/{sym}_5min.parquet")
    df.index = df.index.tz_convert("America/New_York")
    df["d"] = df.index.date
    df["t"] = df.index.strftime("%H:%M")
    days = sorted(df.d.unique())
    op = df.groupby("d").open.first()
    cl = df.groupby("d").close.last()
    piv = df.pivot(index="d", columns="t", values="close")
    sigma = (piv.div(op, axis=0) - 1).abs().rolling(14).mean().shift(1)  # prior sessions only
    prevc = cl.shift(1)
    tp = (df.high + df.low + df.close) / 3 * df.volume
    df["vwap"] = tp.groupby(df.d).cumsum() / df.volume.groupby(df.d).cumsum()
    rows = []
    for d in days[15:]:
        g = df[df.d == d]
        if len(g) < 70:
            continue
        ref = max(op[d], prevc[d])
        pos = None
        for i in range(len(g) - 1):
            r, nxt = g.iloc[i], g.iloc[i + 1]
            s = sigma.loc[d].get(r.t)
            if s is None or np.isnan(s):
                continue
            ub = ref * (1 + s)
            if pos is None:
                if "10:00" <= r.t <= "15:00" and r.t[3:] in ("00", "30") and r.close > ub and r.close > r.vwap:
                    pos = nxt.open
            elif r.close < ub or r.close < r.vwap:
                rows.append((d, nxt.open - pos))
                pos = None
                break
        if pos is not None:
            rows.append((d, g.iloc[-1].close - pos))
    t = pd.DataFrame(rows, columns=["d", "pnl"])
    print(sym, "trades", len(t), "of", len(days) - 15, "sessions |",
          "gross/share %.3f" % t.pnl.mean(), "net/share %.3f" % (t.pnl.mean() - COST),
          "| win%% %.1f" % (100 * (t.pnl > 0).mean()),
          "| gross t-stat %.2f" % (t.pnl.mean() / t.pnl.std() * np.sqrt(len(t))))
