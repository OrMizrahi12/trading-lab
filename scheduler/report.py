"""The short daily report, written at the stop time. Offline: it reads files only and never connects to IB. Paper only."""

import json
from pathlib import Path

import pandas as pd

from live.config import STATE_DIR
from live.state import run_dir

from .jobs import ROOT, Config, DayResult
from .plan import NY


def _lines(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _ny_date(value):
    try:
        ts = pd.Timestamp(value)
        return (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert(NY).date()
    except (ValueError, TypeError):
        return None


def _hhmm(ts) -> str:
    return "-" if ts is None else f"{ts.tz_convert(NY):%H:%M:%S}"


def _state(pair, root: Path) -> dict:
    try:
        return json.loads((run_dir(pair.strategy, pair.symbol, root) / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def status(res: DayResult, cfg: Config, root: Path = STATE_DIR) -> tuple[str, list[str]]:
    """OK, SKIPPED (STOP), HALTED, NOT FLAT or NO RUNNER, with the reasons (a halt or an open position in ANY pair counts)."""
    pairs = cfg.pairs
    states = {p: _state(p, root) for p in pairs}
    runner = next((j for j in res.jobs if j.name == "runner"), None)
    if res.stop_blocked:
        return "SKIPPED (STOP file)", ["the STOP file exists; the scheduler never removes it"]
    if runner is None or runner.started is None:
        return "NO RUNNER", [runner.result if runner else "the runner job did not run"]
    if halted := [f"{p.symbol}: {st['halted']}" for p, st in states.items() if st.get("halted")]:
        return "HALTED", halted
    if open_ := [f"the state file still shows {st['position']['shares']} {p.symbol}; its stop/target rest at IB; "
                 "check IB, the next start reconciles"
                 for p, st in states.items() if st.get("position") and not st["position"].get("closed_at")]:
        return "NOT FLAT", open_
    if runner.result.startswith("stopped early"):
        return "RUNNER STOPPED EARLY", [runner.result]
    return "OK", []


def _log_path(rdir: Path, day) -> Path:
    daily = rdir / "logs" / f"{day}.log"
    return daily if daily.exists() else rdir / "events.log"


def _engine_lines(pair, day, root: Path) -> list[str]:
    rdir = run_dir(pair.strategy, pair.symbol, root)
    signals = [r for r in _lines(rdir / "signals.jsonl") if _ny_date(r.get("seen_at")) == day]
    entries = [r for r in signals if r.get("kind") == "entry"]
    bought = [r for r in entries if r.get("action") == "bought"]
    exits = [r for r in signals if r.get("kind") == "exit"]
    trades = [r for r in _lines(rdir / "trades.jsonl") if _ny_date(r.get("exit_time")) == day]
    out = [f"{pair.strategy} {pair.symbol} {pair.settings or ''}".rstrip(),
           f"  decisions logged: {len(signals)} (entries {len(entries)}, bought {len(bought)}, exits {len(exits)})"]
    for t in trades:
        pnl = (t["exit_price"] - t["entry_price"]) * t["shares"]
        out.append(f"  trade: {t['shares']} @ {t['entry_price']:.2f} -> {t['exit_price']:.2f} ({t['reason']}), {pnl:+.2f} before costs")
    if not trades:
        out.append("  no closed trades today")
    out.append(f"  log: {_log_path(rdir, day)}")
    return out


def build(res: DayResult, cfg: Config, root: Path = STATE_DIR) -> str:
    plan, day = res.plan, res.plan.session
    verdict, why = status(res, cfg, root)
    out = [f"# Daily report {day} ({'half day, closes ' + plan.close.tz_convert(NY).strftime('%H:%M') if plan.half_day else 'full day'})",
           f"Result: {verdict}" + ("".join(f"\n  - {w}" for w in why)), "", "Jobs (New York time)"]
    for j in res.jobs:
        out.append(f"  {j.name:<7} planned {_hhmm(j.planned)}  ran {_hhmm(j.started)} -> {_hhmm(j.ended)}  {j.result}"
                   + (f"  [{Path(j.log_path).name}]" if j.log_path else ""))
    out += [f"  note: {n}" for n in res.runner_notes]

    out += ["", "Gap scan (top 5 by relative volume)"]
    try:
        top = pd.read_csv(ROOT / "watchlists" / f"gap_scan_{day}.csv").head(5)
        out += ["  " + f"{r.symbol:<6} gap {r.gap_pct:+.1f}%  relvol {r.rel_vol:.1f}  ${r.price:.2f}" for r in top.itertuples()] or ["  (empty)"]
    except (OSError, ValueError, AttributeError):
        out.append("  no watchlist for today")

    out += ["", "Engines"]
    for pair in cfg.pairs:
        out += _engine_lines(pair, day, root)
    refusals = [r for r in _lines(root / "risk_refusals.jsonl") if _ny_date(r.get("time")) == day]
    out.append(f"Risk refusals today: {len(refusals)}" + ("".join(f"\n  - {r['symbol']}: {r['reason']}" for r in refusals[:3])))
    return "\n".join(out) + "\n"


def write(res: DayResult, cfg: Config, root: Path = STATE_DIR) -> Path:
    path = root / "reports" / f"{res.plan.session}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(res, cfg, root))
    return path
