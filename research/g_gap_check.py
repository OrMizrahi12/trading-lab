"""Cheap check for idea G: same-day fill rate of gap-down opens by gap size (cache only)."""
import pandas as pd

for sym in ["SPY", "QQQ"]:
    b = pd.read_parquet("data/cache/" + sym + "_5min.parquet")
    b.index = b.index.tz_convert("America/New_York")
    b["d"] = b.index.date
    g = b.groupby("d")
    day = pd.DataFrame({"open": g.open.first(), "close": g.close.last()})
    day["pc"] = day.close.shift()
    day["gap"] = day.open / day.pc - 1
    rows = []
    for d, r in day.dropna().iterrows():
        if r.gap >= 0:
            continue
        bars = g.get_group(d)
        hit = bars.high >= r.pc
        # stop = 1.5 x 2% of gap-size-free proxy: use 0.3% below open (about 1.5 ATR of 5min bars is smaller; report both)
        res = {}
        for name, sp in [("s0.3", 0.003), ("s0.6", 0.006)]:
            stop = r.open * (1 - sp)
            outcome = 0
            for _, bar in bars.iterrows():
                if bar.low <= stop:
                    outcome = -1
                    break
                if bar.high >= r.pc:
                    outcome = 1
                    break
            res[name] = outcome
        rows.append((d, -r.gap, r.pc - r.open, hit.any(), res["s0.3"] == 1, res["s0.6"] == 1))
    x = pd.DataFrame(rows, columns=["d", "ag", "move", "fill", "fill_s0.3", "fill_s0.6"])
    bk = pd.cut(x.ag, [0, .0005, .001, .0025, .005, 1])
    print(sym, "sessions", len(day))
    print(x.groupby(bk, observed=True).agg(n=("fill", "size"), fill=("fill", "mean"),
          fill_s03=("fill_s0.3", "mean"), fill_s06=("fill_s0.6", "mean"), move=("move", "mean")))
