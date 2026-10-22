"""Update the Parquet bar cache: uv run -m data_pipeline [--symbols AAPL SPY] [--bar-sizes 5min 1min] [--backfill-years N] [--dry-run | --quality]"""

import argparse
from datetime import datetime, timedelta, timezone

from . import cache, quality
from .config import BAR_SIZES, SYMBOLS
from .downloader import backfill, connect_paper, download_missing


def dry_run(symbols, bar_sizes, backfill_years=None):
    now = datetime.now(timezone.utc)
    for symbol in symbols:
        for bar_size in bar_sizes:
            spec = BAR_SIZES[bar_size]
            path = cache.cache_path(symbol, bar_size)
            last = cache.edge_timestamp([path, cache.holdout_path(symbol, bar_size)], newest=True)
            if backfill_years:
                target = now - timedelta(days=365.25 * backfill_years)
                oldest = cache.edge_timestamp([path, cache.holdout_path(symbol, bar_size)], newest=False)
                end = now if oldest is None else oldest
                chunks = max(0, -(-(end - target) // spec.chunk_length))
                print(f"{path.name}: would backfill from {end} to {target:%Y-%m-%d} (~{chunks} request(s))")
            elif last is None:
                unit = "trading" if spec.seed_in_trading_days else "calendar"
                print(f"{path.name}: no cache; would seed the last {spec.seed_days} {unit} days")
            else:
                chunks = max(1, -(-(now - last) // spec.chunk_length))
                print(f"{path.name}: cache ends {last}; would fetch bars after it (~{chunks} request(s))")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--bar-sizes", nargs="+", default=list(BAR_SIZES), choices=list(BAR_SIZES))
    parser.add_argument("--backfill-years", type=float, metavar="N", help="walk back N years of history, chunk by chunk; rerun to resume")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without connecting to IB")
    parser.add_argument("--quality", action="store_true", help="check the cached files for bad or missing data (offline)")
    args = parser.parse_args()

    if args.quality:
        quality.run()
        return

    if args.dry_run:
        dry_run(args.symbols, args.bar_sizes, args.backfill_years)
        return
    ib = connect_paper()
    try:
        for symbol in args.symbols:
            for bar_size in args.bar_sizes:
                if args.backfill_years:
                    target = datetime.now(timezone.utc) - timedelta(days=365.25 * args.backfill_years)
                    r = backfill(ib, symbol, bar_size, target)
                else:
                    r = download_missing(ib, symbol, bar_size)
                span = "empty (no bars received)" if r["first"] is None else f"{r['first']} -> {r['last']}"
                held = f", +{r['holdout_added']} held out" if r.get("holdout_added") else ""
                print(f"{r['path'].name}: +{r['added']} bars{held}, cache {span}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
