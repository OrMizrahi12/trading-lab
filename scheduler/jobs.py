"""One trading day: prep, runner (supervised), stop. Paper trading only.

The scheduler places no orders. It starts existing, paper-guarded programs (`data_pipeline`, `scanner.py`,
`live`) as subprocesses and watches them. Everything that touches the clock or a subprocess goes through `Io`,
so the tests run a whole day on a fake clock.

STOP file: the runner is never started while `data/live/STOP` exists and the file is never removed from here.
A halted runner is not resumed (no --clear-halt, no --flatten-on-mismatch).
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from live.config import STATE_DIR
from live.kill import main_precheck
from live.state import run_dir

from .plan import FLATTEN_BEFORE_CLOSE_MIN, NY, DayPlan

log = logging.getLogger("scheduler")
ROOT = Path(__file__).parent.parent
CONFIG = Path(__file__).parent / "schedule.yaml"
POLL_S = 10  # how often the supervisor looks at the runner and the clock
RESTART_WAIT_S = 30
STOP_GRACE_S = 60  # after SIGINT the runner gets this long to save its state before it is terminated
NO_RESTART_CODES = {2: "risk limits file missing or malformed", 3: "STOP file exists"}


@dataclass
class Config:
    strategy: str
    symbol: str
    settings: dict = field(default_factory=dict)
    max_restarts: int = 3

    def runner_cmd(self) -> list[str]:
        cmd = [sys.executable, "-m", "live", "--strategy", self.strategy, "--symbol", self.symbol,
               "--flatten-before-close-min", str(FLATTEN_BEFORE_CLOSE_MIN)]
        for name, value in self.settings.items():
            cmd += ["--set", f"{name}={value}"]
        return cmd


def load_config(path: Path = CONFIG) -> Config:
    raw = yaml.safe_load(path.read_text()) or {}
    unknown = set(raw) - {"strategy", "symbol", "settings", "max_restarts"}
    if unknown or not raw.get("strategy") or not raw.get("symbol"):
        raise ValueError(f"{path}: needs strategy and symbol, allows settings and max_restarts (got {sorted(raw)})")
    return Config(raw["strategy"], raw["symbol"], raw.get("settings") or {}, int(raw.get("max_restarts", 3)))


class SystemIo:
    """The real clock and real subprocesses."""

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def spawn(self, cmd: list[str], log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = log_path.open("ab")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        return subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env, start_new_session=True)

    def interrupt(self, proc) -> None:
        """Ctrl+C for the runner (it saves its state; a stop/target resting at IB stays). Windows has no SIGINT for this."""
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)


@dataclass
class JobRecord:
    name: str
    planned: pd.Timestamp
    started: pd.Timestamp | None = None
    ended: pd.Timestamp | None = None
    result: str = ""  # "ok", "exit 1", "skipped: ...", ...
    log_path: str | None = None


@dataclass
class DayResult:
    plan: DayPlan
    jobs: list[JobRecord] = field(default_factory=list)
    stop_blocked: bool = False  # the STOP file kept the runner from starting
    runner_notes: list[str] = field(default_factory=list)


def _sleep_until(io, when: pd.Timestamp) -> None:
    while (left := (when - io.now()).total_seconds()) > 0:
        io.sleep(min(left, POLL_S))


def _wait(io, proc, deadline: pd.Timestamp) -> int | None:
    """Wait for `proc` until `deadline`; its exit code, or None if it is still running then."""
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        if io.now() >= deadline:
            return None
        io.sleep(POLL_S)


def _run_job(io, rec: JobRecord, cmd: list[str], log_path: Path, deadline: pd.Timestamp) -> None:
    rec.started, rec.log_path = io.now(), str(log_path)
    proc = io.spawn(cmd, log_path)
    rc = _wait(io, proc, deadline)
    if rc is None:
        io.interrupt(proc)
        rc = _wait(io, proc, io.now() + pd.Timedelta(seconds=STOP_GRACE_S))
        rec.result = "timed out (stopped at the runner start time)"
        if rc is None:
            proc.kill()
    else:
        rec.result = "ok" if rc == 0 else f"exit {rc}"
    rec.ended = io.now()


def _halted(root: Path, cfg: Config) -> str | None:
    try:
        return json.loads((run_dir(cfg.strategy, cfg.symbol, root) / "state.json").read_text()).get("halted")
    except (OSError, ValueError):
        return None


def _supervise_runner(io, cfg: Config, plan: DayPlan, res: DayResult, root: Path, log_dir: Path) -> None:
    rec = JobRecord("runner", plan.runner, result="running")
    res.jobs.append(rec)
    log_path = log_dir / f"{plan.session}_runner.log"
    rec.log_path = str(log_path)
    restarts, flatten_noted = 0, False
    proc = None
    try:
        while True:
            if proc is None:
                if main_precheck(root) is not None:  # STOP appeared between restarts
                    res.stop_blocked = True
                    res.runner_notes.append("STOP file present, runner not (re)started")
                    rec.result = "stopped early: STOP file exists"
                    break
                rec.started = rec.started or io.now()
                proc = io.spawn(cfg.runner_cmd(), log_path)
                log.info("runner started (pid %s), restarts so far %d", getattr(proc, "pid", "?"), restarts)
            rc = _wait(io, proc, io.now() + pd.Timedelta(seconds=POLL_S))
            now = io.now()
            if rc is not None:
                proc = None
                why_not = None
                if main_precheck(root) is not None:
                    why_not = "STOP file appeared"
                elif (halt := _halted(root, cfg)) is not None:
                    why_not = f"halted: {halt}"
                elif rc in NO_RESTART_CODES:
                    why_not = f"exit {rc}: {NO_RESTART_CODES[rc]}"
                elif now >= plan.flatten:
                    why_not = "exited after the flatten time"
                elif restarts >= cfg.max_restarts:
                    why_not = f"exited ({rc}) and {restarts} restarts are used up"
                if why_not:
                    res.runner_notes.append(f"runner not restarted: {why_not}")
                    rec.result = f"stopped early: {why_not}"
                    break
                restarts += 1
                res.runner_notes.append(f"runner exited ({rc}) at {now.tz_convert(NY):%H:%M:%S}, restart {restarts}")
                io.sleep(RESTART_WAIT_S)
                continue
            if now >= plan.flatten and not flatten_noted:
                flatten_noted = True
                res.runner_notes.append(f"flatten time {plan.flatten.tz_convert(NY):%H:%M} reached: the runner sells by itself")
            if now >= plan.stop:
                break
        if proc is not None:  # stop time: ask the runner to leave like Ctrl+C
            io.interrupt(proc)
            rc = _wait(io, proc, io.now() + pd.Timedelta(seconds=STOP_GRACE_S))
            if rc is None:
                proc.kill()
                res.runner_notes.append("runner did not leave after Ctrl+C and was killed")
            rec.result = "stopped at the stop time"
            proc = None
    finally:
        if proc is not None and proc.poll() is None:  # the scheduler itself is leaving (Ctrl+C): take the runner with it
            io.interrupt(proc)
            _wait(io, proc, io.now() + pd.Timedelta(seconds=STOP_GRACE_S))
        rec.ended = io.now()


def run_day(plan: DayPlan, cfg: Config, io=None, root: Path = STATE_DIR, out=print) -> DayResult:
    """Run one trading day's jobs in order. Lateness: prep is skipped once the runner time has passed, the runner
    still starts before the flatten time (its reconcile makes a late start safe), the stop always happens."""
    io = io or SystemIo()
    res = DayResult(plan)
    log_dir = root / "scheduler"

    def say(msg):
        out(f"{io.now().tz_convert(NY):%Y-%m-%d %H:%M:%S} NY | {msg}")

    prep = JobRecord("prep", plan.prep)
    res.jobs.append(prep)
    if io.now() >= plan.runner:
        prep.result = "skipped: started after the runner time"
    else:
        _sleep_until(io, plan.prep)
        say("prep: updating the bar cache, then the gap scanner")
        steps = [("data_pipeline", [sys.executable, "-m", "data_pipeline"]), ("scanner", [sys.executable, "scanner.py"])]
        results = []
        for name, cmd in steps:
            sub = JobRecord(f"prep:{name}", plan.prep)
            _run_job(io, sub, cmd, log_dir / f"{plan.session}_{name}.log", plan.runner)
            results.append(f"{name} {sub.result}")
            prep.started = prep.started or sub.started
            prep.ended = sub.ended
            if sub.result.startswith("timed out"):
                break  # no time left for the next step
        prep.result = "; ".join(results)  # a failed prep does not stop the runner: it does not use the watchlist
    say(f"prep: {prep.result}")

    if io.now() >= plan.flatten:
        res.jobs.append(JobRecord("runner", plan.runner, result="skipped: started after the flatten time"))
    elif (why := main_precheck(root)) is not None:
        _sleep_until(io, plan.runner)
        res.stop_blocked = True
        res.jobs.append(JobRecord("runner", plan.runner, result="skipped: STOP file exists"))
        say(f"runner: not started, {why}")
    else:
        _sleep_until(io, plan.runner)
        if (why := main_precheck(root)) is not None:  # STOP written while we waited
            res.stop_blocked = True
            res.jobs.append(JobRecord("runner", plan.runner, result="skipped: STOP file exists"))
            say(f"runner: not started, {why}")
        else:
            say(f"runner: starting {cfg.strategy} {cfg.symbol}")
            _supervise_runner(io, cfg, plan, res, root, log_dir)
            say(f"runner: {res.jobs[-1].result}")
    _sleep_until(io, plan.stop)  # a runner that left early still leaves the report to the stop time
    return res
