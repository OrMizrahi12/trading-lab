"""uv run -m live --strategy opening_range_breakout --symbol QQQ --set range_minutes=15 [--once] [--dry-run]

Runs ONE strategy on ONE symbol against the IB paper Gateway. Paper trading only: the engine refuses any
account that does not start with DU. Nothing here is financial advice.
"""

import argparse
import logging
import sys

from .broker import connect_paper, reconnect_paper
from .config import FLATTEN_BEFORE_CLOSE_MIN, PAPER_PORTS, PORT, STATE_DIR
from .kill import main_precheck
from .registry import STRATEGIES, parse_settings
from .risk import Risk
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
    # The operator's view: console only (the logger does not propagate, so events.log is unchanged).
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    console_log = logging.getLogger("live.console")
    console_log.setLevel(logging.INFO)
    console_log.addHandler(console)


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
    ap.add_argument("--port", type=int, default=PORT, choices=sorted(PAPER_PORTS),
                    help="paper ports only; use 7497 behind a relay to test a dropped connection (see AGENTS.md)")
    ap.add_argument("--clear-halt", action="store_true",
                    help="resume after a halt: only once you have checked IB and the reconcile result is clean")
    ap.add_argument("--flatten-on-mismatch", action="store_true",
                    help="on start, sell shares IB holds that the state file cannot explain (default: halt)")
    ap.add_argument("--flatten-before-close-min", type=int, default=FLATTEN_BEFORE_CLOSE_MIN, metavar="N",
                    help=f"sell any position N minutes before the real close (default {FLATTEN_BEFORE_CLOSE_MIN})")
    args = ap.parse_args()
    if args.catch_up and not args.dry_run:
        ap.error("--catch-up is for --dry-run only: a real run never trades on history")

    stopped = main_precheck(STATE_DIR)
    if stopped:  # before anything connects
        print(stopped)
        sys.exit(3)
    try:
        risk = Risk.from_file()  # live/risk_limits.yaml; a missing or malformed file stops the run, no defaults
    except (OSError, ValueError) as e:
        print(f"risk limits: {e}")
        sys.exit(2)
    settings = parse_settings(args.settings)
    setup_logging(run_dir(args.strategy, args.symbol, STATE_DIR))
    ib = None
    try:
        ib = connect_paper(port=args.port)  # refuses a non-paper port, a remote host or any account not starting with DU
        Runner(ib, args.strategy, args.symbol, settings, dry_run=args.dry_run, catch_up=args.catch_up,
               flatten_on_mismatch=args.flatten_on_mismatch, clear_halt=args.clear_halt, risk=risk, port=args.port,
               flatten_before_close_min=args.flatten_before_close_min,
               reconnect=lambda: reconnect_paper(ib, port=args.port)).run(once=args.once)
    except KeyboardInterrupt:  # Ctrl+C while connecting; Runner.run handles it once running
        print("\nCtrl+C: stopped before the engine started. Nothing was traded.")
        sys.exit(130)
    finally:
        if ib is not None:
            ib.disconnect()


if __name__ == "__main__":
    main()
