"""Dashboard tests: a temporary journal, no IB, no fixed port. The account ID must never reach the output."""

import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from dashboard import data
from dashboard.server import host_is_local, make_server
from live.journal import Journal

ACCOUNT = "DU1234567"
REF_WIN = "live:opening_range_breakout:QQQ:2026-10-21:0945"
REF_LOSS = "live:opening_range_breakout:QQQ:2026-10-22:0950"
REF_OPEN = "live:opening_range_breakout:SPY:2026-10-23:0955"


def _fill(db, exec_id, ref, symbol, side, shares, price, when, commission):
    db.execute(
        """INSERT INTO fills (exec_id, account, symbol, side, shares, price, time_utc, time_et, day, order_ref,
                              entry_ref, commission) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (exec_id, ACCOUNT, symbol, side, shares, price, when, when, when[:10], ref, ref, commission))


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with Journal(path) as j:  # the engine's own class builds the real schema
        db = j.db
        with db:
            _fill(db, "e1", REF_WIN, "QQQ", "BOT", 10, 100.0, "2026-10-21 09:50:00", 1.0)
            _fill(db, "e2", REF_WIN, "QQQ", "SLD", 10, 102.0, "2026-10-21 10:30:00", 1.0)       # +20 - 2 = +18
            _fill(db, "e3", REF_LOSS, "QQQ", "BOT", 10, 100.0, "2026-10-22 09:55:00", 1.0)
            _fill(db, "e4", REF_LOSS, "QQQ", "SLD", 10, 99.0, "2026-10-22 10:10:00", 1.0)       # -10 - 2 = -12
            _fill(db, "e5", REF_OPEN, "SPY", "BOT", 5, 500.0, "2026-10-23 10:00:00", 0.5)       # still open
            db.execute("INSERT INTO snapshots (day, account, open_net_liquidation, net_liquidation) VALUES (?, ?, ?, ?)",
                       ("2026-10-21", ACCOUNT, 1000000.0, 1000018.0))
            db.execute("INSERT INTO snapshots (day, account, open_net_liquidation, net_liquidation) VALUES (?, ?, ?, ?)",
                       ("2026-10-22", ACCOUNT, 1000018.0, 1000006.0))
    return path


def test_trades_are_net_of_commission(journal):
    d = data.load(journal)
    by_ref = {t["entry_ref"]: t for t in d["trades"]}
    assert by_ref[REF_WIN]["gross_pnl"] == 20.0 and by_ref[REF_WIN]["commission"] == 2.0 and by_ref[REF_WIN]["net_pnl"] == 18.0
    assert by_ref[REF_LOSS]["net_pnl"] == -12.0
    assert [t["entry_ref"] for t in d["trades"]] == [REF_LOSS, REF_WIN]  # newest first
    assert REF_OPEN not in by_ref


def test_stats(journal):
    s = data.load(journal)["stats"]
    assert s == {"trades": 2, "win_rate": 50.0, "net_pnl": 6.0, "commissions": 4.0, "pending_commission": 0}


def test_open_position(journal):
    positions = data.load(journal)["positions"]
    assert len(positions) == 1
    p = positions[0]
    assert (p["symbol"], p["shares"], p["side"], p["avg_entry_price"]) == ("SPY", 5, "long", 500.0)


def test_equity_per_day(journal):
    assert data.load(journal)["equity"] == [
        {"day": "2026-10-21", "open": 1000000.0, "close": 1000018.0},
        {"day": "2026-10-22", "open": 1000018.0, "close": 1000006.0}]


def test_missing_commission_is_flagged_not_zeroed(tmp_path):
    path = tmp_path / "j.sqlite3"
    with Journal(path) as j, j.db:
        _fill(j.db, "a", REF_WIN, "QQQ", "BOT", 1, 10.0, "2026-10-21 10:00:00", 0.5)
        _fill(j.db, "b", REF_WIN, "QQQ", "SLD", 1, 11.0, "2026-10-21 10:05:00", None)
    d = data.load(path)
    assert d["trades"][0]["commission_pending"] is True
    assert d["stats"]["pending_commission"] == 1


def test_no_journal_file_is_empty_and_not_created(tmp_path):
    path = tmp_path / "nothing.sqlite3"
    d = data.load(path)
    assert d["available"] is False and d["trades"] == [] and d["stats"]["trades"] == 0
    assert not path.exists()


def test_account_id_never_in_output(journal):
    assert ACCOUNT not in json.dumps(data.load(journal))


def test_connection_is_read_only_and_file_untouched(journal):
    before = hashlib.sha256(journal.read_bytes()).hexdigest()
    db = data.connect(journal)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM fills")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("CREATE TABLE x (a)")
    db.close()
    data.load(journal)
    assert hashlib.sha256(journal.read_bytes()).hexdigest() == before


@pytest.fixture
def server(journal):
    srv = make_server(journal, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(server, path, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}")
    if host:
        req.add_header("Host", host)
    return urllib.request.urlopen(req, timeout=5)


def test_server_binds_localhost_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_server_serves_page_and_json_without_account(server):
    page = _get(server, "/").read().decode()
    assert "Paper engine dashboard" in page and ACCOUNT not in page
    body = _get(server, "/api/data").read().decode()
    assert json.loads(body)["stats"]["trades"] == 2 and ACCOUNT not in body


def test_server_refuses_other_hosts_and_paths(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/api/data", host="evil.example.com")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/data/live/journal.sqlite3")
    assert e.value.code == 404


def test_server_has_no_write_methods(server):
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/data", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 501


@pytest.mark.parametrize("header,ok", [("127.0.0.1:8765", True), ("localhost:8765", True), ("localhost", True),
                                       ("evil.com", False), ("127.0.0.1.evil.com", False), (None, False)])
def test_host_check(header, ok):
    assert host_is_local(header) is ok


# -- the status strip: files only, the desk's folder is read and never written -------------------------------------------

def test_status_reads_lab_and_desk_files_and_survives_missing_ones(tmp_path):
    from datetime import datetime, timezone

    from dashboard import status

    lab, desk = tmp_path / "lab", tmp_path / "desk"
    eng = lab / "opening_range_breakout_QQQ"
    eng.mkdir(parents=True)
    (eng / "state.json").write_text(json.dumps({"halted": None, "position": None}))
    (eng / "bar_delay.csv").write_text("poll_time,bar_start,bar_close,delay_seconds\nx,2026-10-26T13:45:00+00:00,y,1020\n")
    (lab / "STOP").write_text("x")
    (desk / "desk").mkdir(parents=True)
    (desk / "desk" / "state.json").write_text(json.dumps({"halted": None, "open_trade": None, "last_bar_ts": "2026-10-26T13:45:00+00:00"}))
    (desk / "desk" / "journal.jsonl").write_text(json.dumps({"bar": "b", "decision": {"action": "wait"}}) + "\n")
    before = sorted(p.name for p in desk.rglob("*"))
    out = status.load(lab, desk, datetime(2026, 10, 26, 14, 0, tzinfo=timezone.utc))
    assert out["lab"]["engines"][0]["last_bar"]["delay_min"] == 17.0 and out["lab"]["stop_file"] is True
    heartbeat, mcp = out["desk"][0], out["desk"][1]
    assert heartbeat["last_bar_age_min"] == 15.0 and heartbeat["last_entry"]["action"] == "wait"
    assert mcp["file"] is False  # missing file: shown, not an error
    assert sorted(p.name for p in desk.rglob("*")) == before  # nothing created in the desk's folder


def test_status_endpoint_serves_json_on_localhost_only(tmp_path):
    server = make_server(tmp_path / "none.sqlite3", 0, lab_root=tmp_path, desk_root=tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status"))
        assert body["lab"]["engines"] == [] and len(body["desk"]) == 3
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        assert err.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_status_can_hide_an_old_engine_folder_without_touching_it(tmp_path):
    from dashboard import status

    for name in ("opening_range_breakout_QQQ", "opening_range_breakout_SPY"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "state.json").write_text(json.dumps({"halted": "old" if name.endswith("SPY") else None}))
    shown = status.load(tmp_path, tmp_path / "desk", hide=frozenset({"opening_range_breakout_SPY"}))
    assert [e["name"] for e in shown["lab"]["engines"]] == ["opening_range_breakout_QQQ"]
    assert (tmp_path / "opening_range_breakout_SPY" / "state.json").exists()
