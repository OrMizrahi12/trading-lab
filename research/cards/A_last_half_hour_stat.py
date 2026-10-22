import pandas as pd, numpy as np, exchange_calendars as xc
cal=xc.get_calendar("XNYS")
rng=np.random.default_rng(0)
for sym in ["SPY","QQQ"]:
    df=pd.read_parquet(f"data/cache/{sym}_5min.parquet")
    df.index=pd.to_datetime(df.index,utc=True)
    d=df.assign(day=df.index.tz_convert("America/New_York").date)
    rows=[];prev=None
    for day,g in d.groupby("day"):
        try: cl=cal.session_close(pd.Timestamp(day))
        except Exception: continue
        g=g.sort_index()
        sig_start=cl-pd.Timedelta(minutes=35); fill=cl-pd.Timedelta(minutes=30)
        if sig_start not in g.index or fill not in g.index or g.index[-1]!=cl-pd.Timedelta(minutes=5):
            prev=g["close"].iloc[-1]; continue
        p=g.loc[fill,"open"]; c=g["close"].iloc[-1]
        h=g.iloc[:6]
        rows.append(dict(day=day,x=g.loc[sig_start,"close"]/prev-1 if prev else np.nan,
          x1=h["close"].iloc[-1]/h["open"].iloc[0]-1,y=c/p-1))
        prev=g["close"].iloc[-1]
    r=pd.DataFrame(rows).dropna()
    print(sym,len(r),"days; mean y bps",r.y.mean()*1e4)
    def test(name,sig):
        yy=r.y.values; s=sig.values; m=yy[s].mean()
        sh=np.array([rng.permutation(yy)[s].mean() for _ in range(1000)])
        print(f" {name}: n={s.sum()} mean y {m*1e4:.2f} bps, others {yy[~s].mean()*1e4:.2f}, p={(sh>=m).mean():.3f}, hit {(yy[s]>0).mean():.2f}")
    print(" corr x,y",round(r.x.corr(r.y),3)," corr x1,y",round(r.x1.corr(r.y),3))
    test("x>0",r.x>0)
    test("x>0 & big",(r.x>0)&(r.x.abs()>r.x.abs().median()))
    test("x1>0",r.x1>0)
