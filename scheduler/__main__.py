"""uv run -m scheduler [--today [--dry-run] | --next N [--from DATE]]

Runs the live paper engine by itself on NYSE trading days, in New York time:
  09:00 prep (update the bar cache, run the gap scanner)   09:25 start the runner
  close - 5 min (15:55; 12:55 on a half day) the runner flattens   close + 5 min (16:05; 13:05) stop it, write the report

No option: daemon. Sleeps to each trading day's prep time and runs the day, for ever.
--today: run today's session and exit (exits at once if the NYSE is shut today); for systemd timers, Task Scheduler, launchd.
--next N: print the next N trading days with their times (read-only).
Paper trading only. The STOP file is respected and never removed here.
"""

import argparse
import logging
import sys
from datetime import date

import pandas as pd

from live.config import STATE_DIR
from live.kill import main_precheck

from . import report
from .jobs import SystemIo, load_config, run_day
from .plan import NY, next_open_plan, next_plans, plan_for


def utc_offset(ts: pd.Timestamp) -> str:
    hours = ts.tz_convert(NY).utcoffset().total_seconds() / 3600
    return f"UTC{hours:+g}"


def format_plans(plans) -> list[str]:
    rows = [f"{'date':<12}{'':<5}{'prep':<7}{'runner':<8}{'flatten':<9}{'stop':<7}{'zone':<6}{'UTC offset':<12}UTC prep/runner/flatten/stop"]
    for p in plans:
        ny = lambda t: f"{t.tz_convert(NY):%H:%M}"
        utc = lambda t: f"{t:%H:%M}"
        note = f"  half day, closes {ny(p.close)}" if p.half_day else ""
        rows.append(f"{p.session!s:<12}{p.session:%a}  {ny(p.prep):<7}{ny(p.runner):<8}{ny(p.flatten):<9}{ny(p.stop):<7}"
                    f"{p.prep.tz_convert(NY):%Z}{'':<3}{utc_offset(p.prep):<12}"
                    f"{utc(p.prep)}/{utc(p.runner)}/{utc(p.flatten)}/{utc(p.stop)}{note}")
    return rows


def show(plans) -> None:
    print("\n".join(format_plans(plans)))


def dry_run_lines(plan, cfg, root=STATE_DIR) -> list[str]:
    """What `--today --dry-run` prints: the timetable, and what the runner would do given the STOP file."""
    lines = format_plans([plan])
    what = f"{cfg.strategy} {cfg.symbol} {cfg.settings}"
    if (stopped := main_precheck(root)) is not None:
        lines += ["prep would run (it only reads and downloads data).",
                  f"runner would be REFUSED, not started: {stopped}", f"(it would have run: {what})"]
    else:
        lines += ["prep would run.", f"runner would start: {what}"]
    return lines


def do_day(plan, cfg) -> None:
    result = run_day(plan, cfg)
    path = report.write(result, cfg)
    verdict, why = report.status(result, cfg)
    print(f"report: {path} ({verdict})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", action="store_true", help="run today's session, then exit")
    ap.add_argument("--dry-run", action="store_true", help="with --today: show the plan, run nothing")
    ap.add_argument("--next", type=int, metavar="N", help="print the next N trading days and exit")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, metavar="YYYY-MM-DD", help="with --next: start date (default today, New York)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    now = pd.Timestamp.now(tz="UTC")

    if args.next:
        show(next_plans(args.start or now.tz_convert(NY).date(), args.next))
        return
    cfg = load_config()
    if args.today:
        plan = plan_for(now.tz_convert(NY).date())
        if plan is None:
            print(f"{now.tz_convert(NY).date()}: the NYSE is shut today, nothing to run.")
            return
        if args.dry_run:
            print("\n".join(dry_run_lines(plan, cfg)))
            return
        if now >= plan.stop:
            print(f"{plan.session}: the day is already over (stop time {plan.stop.tz_convert(NY):%H:%M} New York).")
            return
        do_day(plan, cfg)
        return
    if args.dry_run:
        ap.error("--dry-run goes with --today")

    print(f"scheduler daemon: {cfg.strategy} {cfg.symbol}; state in {STATE_DIR}; Ctrl+C to leave")
    io = SystemIo()
    while True:
        plan = next_open_plan(io.now())
        show([plan])
        while (left := (plan.prep - io.now()).total_seconds()) > 0:
            io.sleep(min(left, 30))  # short steps so a suspended machine catches up
        try:
            do_day(plan, cfg)
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.getLogger("scheduler").exception("the day %s failed; waiting for the next one", plan.session)
            io.sleep(60)  # never a hot loop if the failure repeats


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nscheduler stopped.")
        sys.exit(130)
