"""ntfy alerts: the sender, the topic in .env, and which engine events do (and do not) raise one. Fake IB, no network."""

import stat
import time

import pytest

from live import alerts
from live.alerts import Alerter, init_topic, read_topic
from live.kill import kill
from live.risk import Risk, load_limits, stop_path
from tests.helpers import DAY1
from tests.test_live_failures import dropping
from tests.test_live_kill import run as run_kill, world as kill_world
from tests.test_live_runner import ENTRY, held, make

TOPIC = "abcdefghijklmnop1234"


class Capture:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, url, body, headers):
        if self.fail:
            raise OSError("network down")
        self.sent.append({"url": url, "text": body.decode(), **headers})

    def kinds(self):
        return [m["Title"].rsplit(": ", 1)[1] for m in self.sent]

    def texts(self):
        return [m["text"] for m in self.sent]


def alerter(**kw):
    cap = Capture(kw.pop("fail", False))
    return Alerter(TOPIC, post=cap, background=False, **kw), cap


# -- the sender ------------------------------------------------------------------------------------------------------

def test_an_alert_is_a_post_to_the_topic_with_ascii_headers():
    a, cap = alerter()
    assert a.send("halt", "QQQ HALTED: something")
    (m,) = cap.sent
    assert m["url"] == f"https://ntfy.sh/{TOPIC}" and m["text"] == "QQQ HALTED: something"
    assert m["Priority"] == "urgent" and m["Tags"] == "rotating_light" and "paper" in m["Title"]
    for value in m.values():
        value.encode("ascii")  # HTTP headers must stay plain ASCII


def test_no_topic_means_alerts_are_off():
    a = Alerter(None, post=Capture())
    assert not a.enabled and a.link is None and a.send("halt", "x") is False


@pytest.mark.parametrize("topic", ["short", "has space 12345678901", "slash/slash/12345678", "x" * 65])
def test_a_weak_or_malformed_topic_is_refused(topic):
    with pytest.raises(ValueError):
        Alerter(topic)


def test_from_env_with_a_bad_topic_turns_alerts_off_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "bad topic")
    assert not Alerter.from_env().enabled


def test_a_keyed_alert_is_sent_once_until_reset():
    a, cap = alerter()
    assert a.send("connection", "lost", key="c") and not a.send("connection", "lost", key="c")
    a.reset("c")
    assert a.send("connection", "lost again", key="c")
    assert len(cap.sent) == 2


def test_long_text_is_cut_and_whitespace_collapsed():
    a, cap = alerter()
    a.send("fill", "a\n  b " + "x" * 1000)
    assert len(cap.sent[0]["text"]) <= alerts.MAX_TEXT and cap.sent[0]["text"].startswith("a b ")


def test_a_failing_network_never_raises_and_is_counted():
    a, cap = alerter(fail=True)
    a.send("halt", "x")
    assert a.failures == 1


def test_background_sending_delivers_and_close_flushes():
    cap = Capture()
    a = Alerter(TOPIC, post=cap)  # the real thread
    for i in range(3):
        a.send("fill", f"n{i}")
    a.close()
    assert cap.texts() == ["n0", "n1", "n2"]


def test_a_slow_network_does_not_block_the_caller():
    def slow(url, body, headers):
        time.sleep(0.5)

    a = Alerter(TOPIC, post=slow)
    started = time.monotonic()
    a.send("halt", "x")
    assert time.monotonic() - started < 0.2
    a.close(timeout=3)


# -- the topic in .env ---------------------------------------------------------------------------------------------------

def test_only_the_ntfy_line_of_env_is_read(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nIB_PASSWORD=hunter2\nNTFY_TOPIC='" + TOPIC + "'\nOTHER=1\n")
    assert read_topic({}, env) == TOPIC
    assert read_topic({"NTFY_TOPIC": "fromenv"}, env) == "fromenv"  # the environment wins
    assert read_topic({}, tmp_path / "missing.env") is None


def test_init_adds_a_random_topic_and_keeps_the_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("IB_PORT=4002")  # no trailing newline
    topic, created = init_topic(env, {})
    assert created and alerts.TOPIC_RE.fullmatch(topic)
    assert env.read_text() == f"IB_PORT=4002\nNTFY_TOPIC={topic}\n"
    again, created = init_topic(env, {})
    assert (again, created) == (topic, False) and env.read_text().count("NTFY_TOPIC") == 1  # never overwritten


def test_init_creates_a_private_file_and_topics_differ(tmp_path):
    a, b = tmp_path / "a.env", tmp_path / "b.env"
    ta, tb = init_topic(a, {})[0], init_topic(b, {})[0]
    assert ta != tb and stat.S_IMODE(a.stat().st_mode) == 0o600


def test_env_file_is_ignored_by_git():
    import subprocess

    assert subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=alerts.ENV_FILE.parent).returncode == 0


# -- what raises an alert -----------------------------------------------------------------------------------------------------

def test_routine_bars_never_alert(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {"10:00": ENTRY}, f"{DAY1} 11:00", last_bar="09:55", alerter=a)  # stale signal, then 'no signal' bars
    runner.start()
    runner.tick()
    runner.tick()
    assert cap.sent == [] and ib.placed == []


def test_a_fill_alerts_once(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", alerter=a)
    runner.start()
    runner.tick()
    assert cap.kinds() == ["fill"] and "BOUGHT" in cap.texts()[0] and "QQQ" in cap.texts()[0]
    runner.tick()
    assert len(cap.sent) == 1


def test_a_stop_hit_alerts_when_ib_closes_it_and_not_again_when_the_bar_is_stepped(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:22", last_bar="10:05", position=held(), holdings=10, alerter=a)
    runner.start()
    ib.now = None
    ib.trigger(ib._trades[0], 98.9)
    runner.tick()
    runner.tick()
    assert cap.kinds() == ["fill"] and "SOLD 10 @ 98.90 (stop)" in cap.texts()[0] and "-11.00 before costs" in cap.texts()[0]


def test_the_wall_clock_flatten_alerts(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 15:51", last_bar="15:45", position=held(), holdings=10, alerter=a)
    runner.start()
    runner.tick()
    assert cap.kinds() == ["fill"] and "flatten_deadline" in cap.texts()[0]


def test_an_ib_rejection_alerts_as_a_reject_and_then_the_halt(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:17", last_bar="10:00", alerter=a)
    ib.reject_types = {"LMT"}
    runner.start()
    runner.tick()
    assert cap.kinds() == ["reject", "halt"]
    assert "IB rejected BUY" in cap.texts()[0] and "[201]" in cap.texts()[0]
    runner.tick()
    assert len(cap.sent) == 2  # a halted runner is silent


def test_a_risk_refusal_alerts_as_a_reject(tmp_path):
    a, cap = alerter()
    risk = Risk(load_limits("symbols:\n  SPY: {max_shares: 5, max_position_value: 1000}\n"
                            "max_trades_per_day: 4\ndaily_loss_limit: 500\n"), root=tmp_path)
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", alerter=a, risk=risk)
    runner.start()
    runner.tick()
    assert cap.kinds() == ["reject"] and "risk limits" in cap.texts()[0] and "QQQ" in cap.texts()[0]
    assert runner.state.halted is None


def test_a_halt_at_start_alerts_once(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", holdings=5, alerter=a)  # IB holds shares the state does not
    runner.start()
    runner.tick()
    runner.tick()
    assert cap.kinds() == ["halt"] and "HALTED" in cap.texts()[0] and "QQQ" in cap.texts()[0]


def test_a_lost_connection_alerts_once_and_again_when_it_is_back(tmp_path):
    a, cap = alerter()
    ib, runner, calls = dropping(tmp_path, alerter=a)
    runner.start()
    ib.connected = False
    runner.tick()
    assert cap.kinds() == ["connection", "connection"]
    assert "Lost" in cap.texts()[0] and "back" in cap.texts()[1]
    ib.connected = False  # a second outage is a new alert
    runner.tick()
    assert cap.kinds() == ["connection"] * 4


def test_a_connection_that_does_not_come_back_alerts_lost_then_halt(tmp_path):
    a, cap = alerter()
    ib, runner, calls = dropping(tmp_path, fail=99, reconnect_attempts=2, alerter=a)
    runner.start()
    ib.connected = False
    runner.tick()
    assert cap.kinds() == ["connection", "halt"]


def test_the_stop_file_appearing_alerts_once(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {}, f"{DAY1} 10:12", last_bar="10:00", alerter=a)
    runner.start()
    stop_path(tmp_path).write_text("kill switch\n")
    runner.tick()
    runner.tick()
    assert cap.kinds() == ["kill"] and runner.stopped


def test_the_kill_switch_command_alerts_with_its_result(tmp_path):
    a, cap = alerter()
    ib = kill_world(tmp_path)
    code, _ = run_kill(ib, tmp_path, alerter=a)
    assert code == 0 and cap.kinds() == ["kill"] and "flat in ['SPY']" in cap.texts()[0]


def test_a_dry_run_never_alerts(tmp_path):
    a, cap = alerter()
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", holdings=5, alerter=a, dry_run=True)
    runner.start()
    runner.tick()
    assert cap.sent == []


def test_a_broken_alert_channel_does_not_stop_trading(tmp_path):
    a, cap = alerter(fail=True)
    ib, runner, _ = make(tmp_path, {"10:05": ENTRY}, f"{DAY1} 10:12", last_bar="10:00", alerter=a)
    runner.start()
    runner.tick()
    assert runner.state.position is not None and a.failures == 1
