"""uv run -m live --strategy opening_range_breakout --symbol QQQ --set range_minutes=15 [--once] [--dry-run]

Runs ONE strategy on ONE symbol against the IB paper Gateway. Paper trading only: the engine refuses any
account that does not start with DU. Nothing here is financial advice.
"""

import argparse
import logging
import sys

from .broker import connect_paper
from .config import STATE_DIR
from .registry import STRATEGIES, parse_settings
from .runner import Runner
from .state import run_dir


def setup_logging(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("live")
    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(directory / "events.log")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--set", dest="settings", action="append", default=[], metavar="name=value",
                    help="a strategy setting, repeatable (same names as the strategy's params)")
    ap.add_argument("--once", action="store_true", help="one poll, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="everything except sending orders; decisions are logged to signals.jsonl")
    ap.add_argument("--catch-up", type=int, default=0, metavar="N",
                    help="dry run only: on a fresh state, also step the last N closed bars to show their decisions")
    ap.add_argument("--clear-halt", action="store_true",
                    help="resume after a halt: only once you have checked IB and the reconcile result is clean")
    ap.add_argument("--flatten-on-mismatch", action="store_true",
                    help="on start, sell shares IB holds that the state file cannot explain (default: halt)")
    args = ap.parse_args()
    if args.catch_up and not args.dry_run:
        ap.error("--catch-up is for --dry-run only: a real run never trades on history")

    settings = parse_settings(args.settings)
    setup_logging(run_dir(args.strategy, args.symbol, STATE_DIR))
    ib = connect_paper()  # refuses a non-paper port, a remote host or any account not starting with DU
    try:
        Runner(ib, args.strategy, args.symbol, settings, dry_run=args.dry_run, catch_up=args.catch_up,
               flatten_on_mismatch=args.flatten_on_mismatch, clear_halt=args.clear_halt).run(once=args.once)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
