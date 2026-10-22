"""The operator's console: start banner, one line per bar, clean Ctrl+C. The log file stays as it was."""

import logging
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from live.runner import Runner
from strategies.base import Entry, Exit
from tests.fake_ib import FakeIB
from tests.helpers import DAY1
from tests.test_live_runner import ENTRY, at, held, make

ROOT = Path(__file__).resolve().parent.parent


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def console():
    handler, log = Capture(), logging.getLogger("live.console")
    old_level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    yield handler
    log.removeHandler(handler)
    log.setLevel(old_level)


def test_start_prints_the_paper_account_and_the_reconcile_result(tmp_path, console):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", position=held(), holdings=10)
    runner.start()
    text = console.text
    assert "DU1234567" in text and "paper account" in text and "client ID 6" in text
    assert "state says long 10 @ 100.00 (stop 99.00" in text
    assert "IB says 10 QQQ; open orders: none" in text
    assert "-> OK" in text
    assert "did: no stop/target found at IB for the open position, placing a new one" in text  # it repaired, and says so


def test_start_prints_the_problem_when_state_and_ib_disagree(tmp_path, console):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", holdings=7)
    runner.start()
    assert "state says flat | IB says 7 QQQ" in console.text
    assert "PROBLEM: State is flat but IB holds 7 QQQ" in console.text and "NOT OK" in console.text


def test_every_new_bar_prints_its_decision_even_when_there_is_none(tmp_path, console):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:22", last_bar="10:00")
    runner.start()
    runner.tick()  # bars 10:05, 10:10, 10:15 closed
    bar_lines = [x for x in console.lines if x.startswith("bar ")]
    assert len(bar_lines) == 3
    assert all(x.endswith("flat | no signal") for x in bar_lines)
    assert "2026-03-02 10:05 ET" in bar_lines[0] and "C 100.00" in bar_lines[0] and "seen 12m after close" in bar_lines[0]


def test_an_entry_and_an_exit_print_what_was_decided_and_done(tmp_path, console):
    ib, runner, clock = make(tmp_path, {"10:05": ENTRY, "10:10": Exit("time")}, f"{DAY1} 10:12", last_bar="10:00")
    runner.start()
    runner.tick()
    entry = next(x for x in console.lines if "10:05 ET" in x)
    assert "ENTRY signal (go, stop 99.00, target none) -> bought" in entry and "stop and target resting at IB" in entry

    clock[0] = at(f"{DAY1} 10:17")
    runner.tick()
    exit_line = next(x for x in console.lines if "10:10 ET" in x)
    assert "long " in exit_line and "EXIT signal (time) -> sold" in exit_line


def test_a_skipped_entry_says_why(tmp_path, console):
    ib, runner, _ = make(tmp_path, {"10:00": ENTRY}, f"{DAY1} 11:00", last_bar="09:55")
    runner.start()
    runner.tick()
    assert any("ENTRY signal" in x and "skipped: signal is" in x for x in console.lines)


def test_console_lines_stay_out_of_the_log_file_logger(tmp_path, console):
    seen = Capture()
    log = logging.getLogger("live")
    log.addHandler(seen)
    try:
        ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00")
        runner.start()
        runner.tick()
    finally:
        log.removeHandler(seen)
    assert not any(x.startswith(("bar ", "account", "reconcile")) for x in seen.lines)


# -- Ctrl+C --------------------------------------------------------------------------------

def test_ctrl_c_during_the_wait_stops_cleanly_saves_state_and_frees_the_lock(tmp_path, console):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", position=held(), holdings=10)

    def interrupted(_seconds):
        raise KeyboardInterrupt

    ib.sleep = interrupted
    runner.run()  # must return, not raise
    assert not (runner.dir / "runner.pid").exists()
    assert runner.state_path.exists()
    assert "Ctrl+C" in console.text and "stop/target stay resting at IB" in console.text


def test_ctrl_c_during_start_is_also_clean(tmp_path, console):
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00")
    ib.qualifyContracts = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt)
    runner.run()
    assert not (runner.dir / "runner.pid").exists() and "Ctrl+C" in console.text


def test_ctrl_c_while_connecting_exits_130_with_a_message(monkeypatch, capsys):
    import live.__main__ as cli

    monkeypatch.setattr(cli, "connect_paper", lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(cli, "setup_logging", lambda directory: None)
    monkeypatch.setattr(sys, "argv", ["live", "--strategy", "ema_trend", "--symbol", "QQQ", "--dry-run"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 130 and "Nothing was traded" in capsys.readouterr().out


def test_a_real_sigint_ends_the_process_with_no_traceback(tmp_path):
    script = textwrap.dedent(f"""
        import logging, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from tests.fake_ib import FakeIB
        from tests.helpers import DAY1, day_bars
        from tests.test_live_runner import Script, at
        from live.feed import BarFeed
        from live.runner import Runner
        from live.__main__ import setup_logging

        ib = FakeIB(); ib.connect("127.0.0.1", 4002)
        ib.sleep = lambda s: time.sleep(0.2)
        bars = day_bars(DAY1, [100.0] * 78, 100.0)
        now = at(DAY1 + " 10:12")
        runner = Runner(ib, "ema_trend", "QQQ", {{}}, strategy=Script({{}}), root=__import__("pathlib").Path({str(tmp_path)!r}),
                        clock=lambda: now, feed=BarFeed(ib, None, clock=lambda: now, fetch=lambda *a: bars))
        setup_logging(runner.dir)
        print("READY", flush=True)
        runner.run()
        print("EXITED CLEANLY", flush=True)
    """)
    proc = subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
    deadline = time.time() + 30
    ready = ""
    while "READY" not in ready and time.time() < deadline:
        ready += proc.stdout.readline()
    time.sleep(1.5)  # let it get into the wait between polls
    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=30)
    out = ready + out
    assert proc.returncode == 0, out
    assert "Traceback" not in out and "EXITED CLEANLY" in out and "Ctrl+C" in out
    assert not (tmp_path / "ema_trend_QQQ" / "runner.pid").exists()
