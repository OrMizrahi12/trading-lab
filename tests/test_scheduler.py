"""The daily scheduler: timetable from the NYSE calendar, a whole day on a fake clock, STOP, restarts, the report."""

import json
from datetime import date

import pandas as pd
import pytest

from live.risk import stop_path
from scheduler import report
from scheduler.jobs import Config, DayResult, load_config, run_day
from scheduler.plan import NY, next_open_plan, next_plans, plan_for

CFG = Config("opening_range_breakout", "QQQ", {"range_minutes": 15}, max_restarts=3)


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def ny(text):
    return pd.Timestamp(text, tz=NY).tz_convert("UTC")


# -- the timetable -----------------------------------------------------------------------------------------------

def test_full_day_in_summer_time():
    p = plan_for(date(2026, 10, 23))  # EDT, UTC-4
    assert (p.prep, p.runner, p.flatten, p.stop) == (utc("2026-10-23 13:00"), utc("2026-10-23 13:25"),
                                                     utc("2026-10-23 19:55"), utc("2026-10-23 20:05"))
    assert not p.half_day


def test_full_day_in_winter_time():
    p = plan_for(date(2026, 12, 1))  # EST, UTC-5
    assert (p.prep, p.runner, p.flatten, p.stop) == (utc("2026-12-01 14:00"), utc("2026-12-01 14:25"),
                                                     utc("2026-12-01 20:55"), utc("2026-12-01 21:05"))


def test_half_day_ends_early():
    p = plan_for(date(2026, 11, 27))  # day after Thanksgiving, closes 13:00
    assert p.half_day
    assert p.flatten == ny("2026-11-27 12:55") and p.stop == ny("2026-11-27 13:05")
    assert p.prep == ny("2026-11-27 09:00")


@pytest.mark.parametrize("day", [date(2026, 11, 26), date(2026, 12, 25), date(2026, 10, 24), date(2026, 10, 25)])
def test_holidays_and_weekends_have_no_plan(day):
    assert plan_for(day) is None


def test_next_plans_skip_thanksgiving_and_weekend():
    days = [p.session for p in next_plans(date(2026, 11, 24), 5)]
    assert days == [date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1)]


@pytest.mark.parametrize("before,after", [(date(2026, 10, 30), date(2026, 11, 2)), (date(2027, 3, 12), date(2027, 3, 15))])
def test_new_york_times_hold_across_the_clock_change(before, after):
    a, b = plan_for(before), plan_for(after)
    for field in ("prep", "runner", "flatten", "stop"):
        ta, tb = getattr(a, field).tz_convert(NY), getattr(b, field).tz_convert(NY)
        assert ta.time() == tb.time()
    assert b.prep - pd.Timestamp(after, tz="UTC") != a.prep - pd.Timestamp(before, tz="UTC")  # UTC moved by an hour


def test_next_open_plan_today_while_running_else_next_session():
    assert next_open_plan(ny("2026-10-23 12:00")).session == date(2026, 10, 23)
    assert next_open_plan(ny("2026-10-23 16:30")).session == date(2026, 10, 26)  # Friday evening -> Monday


# -- a whole day on a fake clock -----------------------------------------------------------------------------------

class Proc:
    def __init__(self, io, die_after_s=None, rc=0, pid=1):
        self.io, self.rc, self.pid = io, rc, pid
        self.dies_at = None if die_after_s is None else io.t + pd.Timedelta(seconds=die_after_s)
        self.interrupted = self.killed = False

    def poll(self):
        if self.killed or self.interrupted:
            return 0
        return self.rc if self.dies_at is not None and self.io.t >= self.dies_at else None

    def kill(self):
        self.killed = True


class FakeIo:
    def __init__(self, start, runners=()):
        self.t, self.spawned, self.runners = start, [], list(runners)

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += pd.Timedelta(seconds=max(seconds, 0.001))

    def spawn(self, cmd, log_path):
        self.spawned.append((self.t, cmd))
        if "live" in cmd:
            return self.runners.pop(0) if self.runners else Proc(self)
        return Proc(self, die_after_s=5)  # prep steps finish quickly

    def interrupt(self, proc):
        proc.interrupted = True


def commands(io):
    return [" ".join(c[1:]) for _, c in io.spawned]


def test_whole_day_runs_prep_runner_and_stops_it(tmp_path):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 08:50"))
    runner = Proc(io)
    io.runners = [runner]
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    steps = commands(io)
    assert steps[0] == "-m data_pipeline" and steps[1] == "scanner.py"
    assert steps[2] == ("-m live --strategy opening_range_breakout --symbol QQQ --flatten-before-close-min 5 "
                        "--set range_minutes=15")
    assert "--clear-halt" not in steps[2] and "--flatten-on-mismatch" not in steps[2]
    times = [t.tz_convert(NY).strftime("%H:%M") for t, _ in io.spawned]
    assert times[0] == "09:00" and times[2] == "09:25"
    assert runner.interrupted and io.t >= plan.stop
    assert [j.name for j in res.jobs] == ["prep", "runner"] and res.jobs[1].result == "stopped at the stop time"
    assert any("flatten time 15:55" in n for n in res.runner_notes)
    assert io.t.tz_convert(NY).strftime("%H:%M") == "16:05"


def test_half_day_stops_at_13_05(tmp_path):
    plan = plan_for(date(2026, 11, 27))
    io = FakeIo(ny("2026-11-27 08:55"))
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert any("flatten time 12:55" in n for n in res.runner_notes)
    assert io.t.tz_convert(NY).strftime("%H:%M") == "13:05"


def test_stop_file_blocks_the_runner_and_is_left_alone(tmp_path):
    stop = stop_path(tmp_path)
    stop.write_text("kill switch test\n")
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 08:55"))
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert commands(io) == ["-m data_pipeline", "scanner.py"]  # prep still ran, no runner
    assert res.stop_blocked and stop.read_text() == "kill switch test\n"


def test_stop_file_appearing_during_the_day_is_not_restarted_over(tmp_path):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 09:20"))
    runner = Proc(io, die_after_s=3600, rc=0)
    io.runners = [runner]

    real_sleep = io.sleep

    def sleep(s):
        real_sleep(s)
        if io.t >= ny("2026-10-23 10:00"):
            stop_path(tmp_path).write_text("x")

    io.sleep = sleep
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert sum("live" in c for _, c in io.spawned) == 1
    assert stop_path(tmp_path).exists() and "STOP" in res.jobs[1].result


def test_runner_that_keeps_dying_is_restarted_three_times_then_reported(tmp_path):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 09:20"))
    io.runners = [Proc(io, die_after_s=20, rc=1) for _ in range(10)]
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert sum("live" in c for _, c in io.spawned) == 4  # first start + 3 restarts
    assert "restarts are used up" in res.jobs[1].result


def test_halted_runner_is_not_restarted(tmp_path):
    d = tmp_path / "opening_range_breakout_QQQ"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"halted": "reconcile: IB holds 3 QQQ", "position": None}))
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 09:20"))
    io.runners = [Proc(io, die_after_s=20, rc=1)]
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert sum("live" in c for _, c in io.spawned) == 1 and "halted" in res.jobs[1].result


@pytest.mark.parametrize("code", [2, 3])
def test_exit_codes_for_risk_file_and_stop_are_not_retried(tmp_path, code):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 09:20"))
    io.runners = [Proc(io, die_after_s=20, rc=code)]
    run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert sum("live" in c for _, c in io.spawned) == 1


def test_late_start_skips_prep_but_still_runs_the_runner(tmp_path):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 11:00"))
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert res.jobs[0].result.startswith("skipped") and commands(io)[0].startswith("-m live")


def test_start_after_the_flatten_time_does_not_start_a_runner(tmp_path):
    plan = plan_for(date(2026, 10, 23))
    io = FakeIo(ny("2026-10-23 15:58"))
    res = run_day(plan, CFG, io, root=tmp_path, out=lambda m: None)
    assert io.spawned == [] and "flatten time" in res.jobs[1].result


def test_config_file_loads_and_rejects_typos(tmp_path):
    cfg = load_config()
    assert cfg.strategy == "opening_range_breakout" and cfg.symbol == "QQQ"
    bad = tmp_path / "x.yaml"
    bad.write_text("strategy: a\nsymbol: B\nsetings: {}\n")
    with pytest.raises(ValueError):
        load_config(bad)


# -- the report ------------------------------------------------------------------------------------------------------

def finished(tmp_path, state, *, stop_blocked=False, runner_result="stopped at the stop time"):
    plan = plan_for(date(2026, 10, 23))
    d = tmp_path / "opening_range_breakout_QQQ"
    d.mkdir(exist_ok=True)
    (d / "state.json").write_text(json.dumps(state))
    res = DayResult(plan, stop_blocked=stop_blocked)
    from scheduler.jobs import JobRecord

    res.jobs = [JobRecord("prep", plan.prep, plan.prep, plan.prep, "data_pipeline ok; scanner ok"),
                JobRecord("runner", plan.runner, plan.runner, plan.stop, runner_result)]
    return res


def test_report_flat_day(tmp_path):
    res = finished(tmp_path, {"halted": None, "position": None})
    d = tmp_path / "opening_range_breakout_QQQ"
    (d / "signals.jsonl").write_text(
        json.dumps({"kind": "entry", "action": "bought", "seen_at": "2026-10-23T14:10:00+00:00"}) + "\n"
        + json.dumps({"kind": "exit", "action": "sold", "seen_at": "2026-10-23T19:40:00+00:00"}) + "\n"
        + json.dumps({"kind": "entry", "action": "bought", "seen_at": "2026-10-22T14:10:00+00:00"}) + "\n")  # yesterday
    (d / "trades.jsonl").write_text(json.dumps(
        {"entry_time": "2026-10-23T14:15:00+00:00", "entry_price": 500.0, "shares": 10, "exit_time": "2026-10-23T19:40:00+00:00",
         "exit_price": 501.5, "reason": "flatten"}) + "\n")
    text = report.build(res, CFG, tmp_path)
    assert "Result: OK" in text and "entries 1, bought 1, exits 1" in text and "+15.00 before costs" in text
    assert "09:00:00" in text and "full day" in text


def test_report_not_flat_is_a_failure(tmp_path):
    res = finished(tmp_path, {"halted": None, "position": {"shares": 10, "closed_at": None}})
    assert "Result: NOT FLAT" in report.build(res, CFG, tmp_path)


def test_report_position_already_closed_at_ib_counts_as_flat(tmp_path):
    res = finished(tmp_path, {"halted": None, "position": {"shares": 10, "closed_at": "2026-10-23T15:00:00+00:00"}})
    assert "Result: OK" in report.build(res, CFG, tmp_path)


def test_report_halted_and_stop(tmp_path):
    assert "Result: HALTED" in report.build(finished(tmp_path, {"halted": "boom", "position": None}), CFG, tmp_path)
    assert "SKIPPED (STOP file)" in report.build(finished(tmp_path, {"halted": None, "position": None}, stop_blocked=True),
                                                 CFG, tmp_path)


def test_report_is_written_to_the_reports_folder(tmp_path):
    res = finished(tmp_path, {"halted": None, "position": None})
    path = report.write(res, CFG, tmp_path)
    assert path == tmp_path / "reports" / "2026-10-23.md" and path.read_text().startswith("# Daily report 2026-10-23")
