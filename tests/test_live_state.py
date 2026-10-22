"""State file: round trip, atomic write, settings guard, runner lock."""

import math
import os

import pytest

from live.state import (LiveState, PositionState, StateError, load, lock, run_dir, save,
                        settings_key)


def make_state(**kw):
    key = settings_key("ema_trend", "QQQ", {"fast_period": 20})
    return LiveState("ema_trend", "QQQ", key, **kw)


def test_round_trip_with_position(tmp_path):
    path = tmp_path / "state.json"
    pos = PositionState(shares=10, entry_time="2026-03-02T15:00:00+00:00", entry_price=100.5, stop=99.0,
                        target=None, bars_held=3, stop_order_id=7)
    save(path, make_state(last_bar_ts="2026-03-02T14:55:00+00:00", position=pos))
    back = load(path, "ema_trend", "QQQ", make_state().key)
    assert back.position == pos
    assert back.last_bar_ts == "2026-03-02T14:55:00+00:00"
    p = back.position.to_position()
    assert p.target == math.inf and p.bars_held == 3 and p.stop == 99.0
    assert str(p.entry_time) == "2026-03-02 15:00:00+00:00"


def test_missing_file_gives_fresh_flat_state(tmp_path):
    state = load(tmp_path / "none.json", "ema_trend", "QQQ", "k")
    assert state.position is None and state.last_bar_ts is None


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "sub" / "state.json"
    save(path, make_state())
    save(path, make_state(last_bar_ts="x"))
    assert sorted(p.name for p in path.parent.iterdir()) == ["state.json"]


def test_state_of_other_settings_is_refused(tmp_path):
    path = tmp_path / "state.json"
    save(path, make_state())
    with pytest.raises(StateError):
        load(path, "ema_trend", "QQQ", settings_key("ema_trend", "QQQ", {"fast_period": 30}))


def test_unknown_version_is_refused(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(make_state().to_json().replace('"version": 1', '"version": 99'))
    with pytest.raises(StateError):
        load(path, "ema_trend", "QQQ", make_state().key)


def test_lock_blocks_a_second_live_runner_and_frees_on_exit(tmp_path):
    d = run_dir("s", "QQQ", tmp_path)
    with lock(d):
        (d / "runner.pid").write_text(str(os.getppid()))  # pretend another live process holds it
        with pytest.raises(StateError):
            with lock(d):
                pass
        (d / "runner.pid").write_text(str(os.getpid()))
    assert not (d / "runner.pid").exists()


def test_lock_from_dead_process_is_taken_over(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "runner.pid").write_text("999999999")
    with lock(d):
        assert (d / "runner.pid").read_text() == str(os.getpid())
