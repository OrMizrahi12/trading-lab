import pandas as pd, numpy as np
rng=np.random.default_rng(0)
for sym in ["SPY","QQQ"]:
    df=pd.read_parquet(f"data/cache/{sym}_5min.parquet"); df.index=pd.to_datetime(df.index,utc=True)
    df["day"]=df.index.tz_convert("America/New_York").date
    g=df.groupby("day").agg(o=("open","first"),c=("close","last"))
    g["prev"]=g.c.shift(1)
    g["on"]=np.log(g.o/g.prev); g["id"]=np.log(g.c/g.o); g["cc"]=np.log(g.c/g.prev)
    g=g.dropna()
    print(sym,len(g),"days; total buy&hold close-to-close",round(np.exp(g.cc.sum())-1,4))
    for k in ["on","id","cc"]:
        x=g[k].values; m=x.mean(); t=m/(x.std(ddof=1)/np.sqrt(len(x)))
        sh=np.array([(x*rng.choice([-1,1],len(x))).mean() for _ in range(1000)])
        print(f" {k}: sum {x.sum()*100:.2f}%, mean {m*1e4:.2f} bps/day, t={t:.2f}, hit {(x>0).mean():.2f}, p(sign-flip)={(sh>=m).mean():.3f}")
    print(" corr on,id",round(g.on.corr(g.id),3))
