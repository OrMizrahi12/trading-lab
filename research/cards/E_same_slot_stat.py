import pandas as pd, numpy as np
rng=np.random.default_rng(0)
for sym in ["SPY","QQQ","AAPL"]:
    df=pd.read_parquet(f"data/cache/{sym}_5min.parquet"); df.index=pd.to_datetime(df.index,utc=True)
    ny=df.index.tz_convert("America/New_York"); df=df.assign(day=ny.date,m=ny.hour*60+ny.minute)
    rows={}
    for day,g in df.groupby("day"):
        if len(g)!=78 or g.m.iloc[0]!=570 or g.m.iloc[-1]!=955: continue
        o=g.open.values.reshape(13,6)[:,0]; c=g.close.values.reshape(13,6)[:,-1]
        rows[day]=c/o-1
    R=pd.DataFrame(rows).T.sort_index()  # days x 13
    Z=((R-R.mean())/R.std()).values; n=len(Z)
    print(sym,n,"full days")
    def pooled(Z,lag):
        return np.corrcoef(Z[lag:].ravel(),Z[:-lag].ravel())[0,1]
    obs=pooled(Z,1)
    sh=[]
    for _ in range(1000):
        Zs=np.column_stack([rng.permutation(Z[:,k]) for k in range(13)]); sh.append(pooled(Zs,1))
    sh=np.array(sh); print(f" pooled lag1 corr {obs:.4f}  p(|.|)={(abs(sh)>=abs(obs)).mean():.3f}  (n pairs {(n-1)*13})")
    print(" lags2-5:",[round(pooled(Z,l),3) for l in range(2,6)])
    ctrl=np.mean([np.corrcoef(Z[1:,k],Z[:-1,j])[0,1] for k in range(13) for j in range(13) if j!=k]); print(f" control other-slot lag1 mean corr {ctrl:.4f}")
    for k in range(13):
        r=np.corrcoef(Z[1:,k],Z[:-1,k])[0,1]
        s=np.array([np.corrcoef(rng.permutation(Z[1:,k]),Z[:-1,k])[0,1] for _ in range(1000)])
        print(f"  slot {k:2d} ({(570+30*k)//60}:{(570+30*k)%60:02d}) r={r:+.3f} p={(abs(s)>=abs(r)).mean():.3f}")
