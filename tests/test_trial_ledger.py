"""Offline tests for the judge's trial ledger and audit log (judge/scorecard.py). No backtests are run."""

import csv
import json
import subprocess

import pytest

import judge.scorecard as sc
from judge.scorecard import Rule, effective_trials, record_trial, settings_key

ORB = "opening_range_breakout"


def ledger_rows(path):
    return list(csv.DictReader(path.open()))


def test_settings_key_fills_in_defaults_and_ignores_order():
    default = sc.STRATEGIES[ORB].params().range_minutes
    assert settings_key(ORB, {}) == settings_key(ORB, {"range_minutes": default})  # same strategy, one trial
    assert settings_key(ORB, {"range_minutes": default + 5}) != settings_key(ORB, {})
    assert settings_key(ORB, {"range_minutes": 15, "target_r": 1.0}) == settings_key(ORB, {"target_r": 1.0, "range_minutes": 15})


def test_ledger_counts_distinct_settings_per_strategy(tmp_path):
    path = tmp_path / "trials.csv"
    assert record_trial(path, ORB, "QQQ", '{"a": 1}', "abc", tmp_path) == 1
    assert record_trial(path, ORB, "QQQ", '{"a": 2}', "abc", tmp_path) == 2
    assert record_trial(path, ORB, "SPY", '{"a": 2}', "abc", tmp_path) == 3  # another symbol is another test
    assert record_trial(path, "donchian_breakout", "QQQ", '{"a": 9}', "abc", tmp_path) == 1  # counted per strategy
    assert record_trial(path, ORB, "QQQ", '{"a": 1}', "abc", tmp_path) == 3  # a rerun is not a new trial...
    rows = ledger_rows(path)
    assert len(rows) == 5 and list(rows[0]) == sc.LEDGER_FIELDS  # ...but it is still written down


def test_ledger_survives_a_missing_directory_and_an_empty_file(tmp_path):
    path = tmp_path / "research" / "trials.csv"
    assert record_trial(path, ORB, "QQQ", "{}", "abc", tmp_path) == 1
    empty = tmp_path / "empty.csv"
    empty.touch()
    assert record_trial(empty, ORB, "QQQ", "{}", "abc", tmp_path) == 1
    assert len(ledger_rows(empty)) == 1


@pytest.mark.parametrize("claimed,assumed,ledger,expected", [
    (None, 100, 3, (100, "assumed")),
    (42, 100, 3, (42, "claimed")),      # a claim below the assumed default is still the user's call
    (5, 100, 30, (30, "ledger")),       # the ledger beats a low claim
    (None, 20, 30, (30, "ledger")),
    (500, 100, 30, (500, "claimed")),   # a claim can always raise the count
    (30, 100, 30, (30, "claimed")),
])
def test_effective_trials_only_goes_up(claimed, assumed, ledger, expected):
    assert effective_trials(claimed, assumed, ledger) == expected


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def test_ledger_refuses_to_run_when_committed_rows_were_deleted(tmp_path):
    git(tmp_path, "init", "-q")
    path = tmp_path / "research" / "trials.csv"
    for i in range(3):
        record_trial(path, ORB, "QQQ", f'{{"a": {i}}}', "abc", tmp_path)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "ledger")

    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:2]))  # header + one trial: two trials erased
    with pytest.raises(RuntimeError, match="trials were removed"):
        record_trial(path, ORB, "QQQ", '{"a": 9}', "abc", tmp_path)
    assert path.read_text() == "".join(lines[:2])  # and it wrote nothing

    path.write_text("".join(lines) )  # restored: fine again, appends one
    assert record_trial(path, ORB, "QQQ", '{"a": 9}', "abc", tmp_path) == 4


def test_git_state_outside_a_repo_is_unknown(tmp_path):
    assert sc.git_state(tmp_path) == {"commit": "unknown", "dirty": None}


def test_git_state_ignores_the_two_record_files(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "research").mkdir()
    (tmp_path / "code.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "c")
    (tmp_path / "research" / "trials.csv").write_text("new\n")
    (tmp_path / "research" / "audit_log.jsonl").write_text("{}\n")
    assert sc.git_state(tmp_path)["dirty"] is False
    (tmp_path / "code.py").write_text("x = 2\n")
    assert sc.git_state(tmp_path)["dirty"] is True


# --- scorecard() with the heavy part replaced -----------------------------

def fake_card(**_):
    return {"passed": True, "seconds": 1.234, "rules": [Rule("Trades", "250", ">= 200", True)]}


@pytest.fixture
def judged(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_evaluate", lambda *a, **k: fake_card())
    monkeypatch.setattr(sc, "git_state", lambda root=None: {"commit": "deadbeef", "dirty": False})
    return tmp_path / "research"


def audit_lines(research):
    return [json.loads(line) for line in (research / "audit_log.jsonl").read_text().splitlines()]


def test_every_run_appends_one_audit_line(judged):
    sc.scorecard(ORB, "QQQ", {"range_minutes": 15}, trials=42, research_dir=judged, log=lambda s: None)
    sc.scorecard(ORB, "SPY", {}, research_dir=judged, log=lambda s: None)
    first, second = audit_lines(judged)
    assert first["commit"] == "deadbeef" and first["dirty"] is False
    assert first["strategy"] == ORB and first["symbol"] == "QQQ" and first["settings"] == {"range_minutes": 15}
    assert first["seed"] == {"walk_forward": 7, "monte_carlo": 42, "noise": 11}
    assert first["trials"] == {"claimed": 42, "assumed": 100, "ledger": 1, "used": 42, "basis": "claimed"}
    assert first["result"]["passed"] is True and first["result"]["rules"][0]["name"] == "Trades"
    assert second["trials"]["claimed"] is None and second["trials"]["basis"] == "assumed"
    assert {"time", "command", "standards_sha256"} <= set(first)


def test_a_low_claim_cannot_lower_the_count_below_the_ledger(judged):
    for i in range(5):
        sc.scorecard(ORB, "QQQ", {"range_minutes": 5 + 5 * i}, trials=1, research_dir=judged, log=lambda s: None)
    last = audit_lines(judged)[-1]["trials"]
    assert last == {"claimed": 1, "assumed": 100, "ledger": 5, "used": 5, "basis": "ledger"}
    assert len(ledger_rows(judged / "trials.csv")) == 5


def test_a_crashed_run_still_counts_and_is_logged(judged, monkeypatch):
    def boom(*a, **k):
        raise ValueError("no bars")
    monkeypatch.setattr(sc, "_evaluate", boom)
    with pytest.raises(ValueError):
        sc.scorecard(ORB, "QQQ", {}, research_dir=judged, log=lambda s: None)
    assert len(ledger_rows(judged / "trials.csv")) == 1
    assert "ValueError" in audit_lines(judged)[0]["result"]["error"]


def test_the_standards_hash_changes_when_a_threshold_does(judged):
    std = sc.load_standards()
    sc.scorecard(ORB, "QQQ", {}, std, research_dir=judged, log=lambda s: None)
    std["rules"]["min_trades"] = 20
    sc.scorecard(ORB, "QQQ", {}, std, research_dir=judged, log=lambda s: None)
    a, b = audit_lines(judged)
    assert a["standards_sha256"] != b["standards_sha256"]
