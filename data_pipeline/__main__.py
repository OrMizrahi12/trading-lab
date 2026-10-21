"""Update the Parquet bar cache: uv run -m data_pipeline [--symbols AAPL SPY] [--bar-sizes 5min 1min] [--dry-run]"""

import argparse
from datetime import datetime, timedelta, timezone

from . import cache
from .config import BAR_SIZES, SYMBOLS
from .downloader import connect_paper, download_missing


def dry_run(symbols, bar_sizes):
    now = datetime.now(timezone.utc)
    for symbol in symbols:
        for bar_size in bar_sizes:
            spec = BAR_SIZES[bar_size]
            path = cache.cache_path(symbol, bar_size)
            last = cache.last_timestamp(path)
            if last is None:
                unit = "trading" if spec.seed_in_trading_days else "calendar"
                print(f"{path.name}: no cache; would seed the last {spec.seed_days} {unit} days")
            else:
                chunks = max(1, -(-(now - last) // spec.chunk_length))
                print(f"{path.name}: cache ends {last}; would fetch bars after it (~{chunks} request(s))")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--bar-sizes", nargs="+", default=list(BAR_SIZES), choices=list(BAR_SIZES))
    parser.add_argument("--dry-run", action="store_true", help="show the plan without connecting to IB")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(args.symbols, args.bar_sizes)
        return
    ib = connect_paper()
    try:
        for symbol in args.symbols:
            for bar_size in args.bar_sizes:
                r = download_missing(ib, symbol, bar_size)
                span = "empty (no bars received)" if r["first"] is None else f"{r['first']} -> {r['last']}"
                print(f"{r['path'].name}: +{r['added']} bars, cache {span}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
