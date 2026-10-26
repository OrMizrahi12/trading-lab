"""Read-only status of everything running on this machine, from files only. Paper trading only.

Lab engines: `data/live/<strategy>_<symbol>/state.json` and `bar_delay.csv`, the STOP file, today's risk refusals.
Desk (~/trading-desk, never edited, never imported, no IB connection): `data/desk/state.json` (heartbeat),
`data/mcp/state.json`, `data/open/state.json` and the last line of each `journal.jsonl`. Every file is opened for
reading only; a missing or unreadable one shows as "no file" instead of failing the page.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent / "data" / "live"
DESK_ROOT = Path.home() / "trading-desk" / "data"
DESK_PARTS = {"heartbeat (client 8)": "desk", "MCP server (client 20)": "mcp", "open-window agent (client 10)": "open"}


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _last_line(path: Path, max_bytes: int = 65536) -> str | None:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode(errors="replace").strip().splitlines()
    except OSError:
        return None
    return lines[-1] if lines else None


def _age_min(ts: str | None, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return round((now - when).total_seconds() / 60, 1)


def _last_delay(path: Path) -> dict | None:
    line = _last_line(path)
    if not line or line.startswith("poll_time"):
        return None
    row = next(csv.reader([line]), [])
    return {"bar_start": row[1], "delay_min": round(float(row[3]) / 60, 1)} if len(row) == 4 else None


def lab(root: Path, now: datetime) -> dict:
    engines = []
    for state_file in sorted(Path(root).glob("*/state.json")):
        state = _json(state_file) or {}
        engines.append({"name": state_file.parent.name, "halted": state.get("halted"),
                        "position": state.get("position"), "last_bar": _last_delay(state_file.parent / "bar_delay.csv")})
    refusals = Path(root) / "risk_refusals.jsonl"
    today = now.strftime("%Y-%m-%d")
    refused = 0
    try:
        refused = sum(1 for line in refusals.read_text().splitlines() if today in line[:60])
    except OSError:
        pass
    return {"engines": engines, "stop_file": (Path(root) / "STOP").exists(), "risk_refusals_today": refused}


def desk(root: Path, now: datetime) -> list[dict]:
    out = []
    for label, sub in DESK_PARTS.items():
        state = _json(Path(root) / sub / "state.json")
        last = _last_line(Path(root) / sub / "journal.jsonl")
        row = {"name": label, "file": state is not None}
        if state is not None:
            row.update({"halted": state.get("halted"), "open_trade": state.get("open_trade"),
                        "session_cost_usd": state.get("session_cost_usd"),
                        "last_bar": state.get("last_bar_ts"), "last_bar_age_min": _age_min(state.get("last_bar_ts"), now)})
        try:
            rec = json.loads(last) if last else {}
            row["last_entry"] = {"at": rec.get("bar") or rec.get("at"), "action": (rec.get("decision") or {}).get("action")
                                 or rec.get("outcome")}
        except ValueError:
            row["last_entry"] = None
        out.append(row)
    return out


def load(lab_root: Path = LAB_ROOT, desk_root: Path = DESK_ROOT, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"as_of": now.isoformat(), "lab": lab(lab_root, now), "desk": desk(desk_root, now)}
