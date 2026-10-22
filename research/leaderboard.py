"""Leaderboard: every strategy run the judge has scored, ranked, with duplicates and look-alikes removed.

    uv run -m research.leaderboard                 # writes research/leaderboard.md
    uv run -m research.leaderboard --threshold 0.8 --out /tmp/board.md
    uv run -m research.leaderboard --no-returns    # skip the backtests: exact-duplicate removal only

Paper research only. Nothing here touches a broker. Nothing here is financial advice.

Sources (read only, nothing is written to them):
1. research/audit_log.jsonl: one line per judge run, written by the judge itself. The trusted source.
2. research/cards/*.md: full "SCORECARD ..." blocks pasted into strategy cards. This is the only record of runs made
   before the audit log existed. Cards that only hold a summary table are not machine-readable and are counted but skipped.

Steps:
1. Read both. Runs that crashed (audit log "error") are counted, not ranked.
2. Drop exact duplicates: same strategy, same symbol, same full settings (defaults filled in). Audit log beats card;
   later beats earlier.
3. Rank: most rules passed first, then Deflated Sharpe, then Sharpe after costs.
4. Walking down the ranking, drop any run whose daily returns correlate above the threshold (default 0.7) with a run
   already kept (so with a better one). The daily returns are not in the audit log, so each kept candidate is re-run once
   with the judge's own settings and usual costs on the research cache (never the holdout) and cached in
   research/leaderboard_cache/ (gitignored). A run whose returns can't be rebuilt stays on the board, marked.

Plain English: "correlation" is how closely two daily-profit lines move together (1 = same, 0 = unrelated). Two strategies
at 0.9 are really one idea twice. "Sharpe" is return per unit of ups and downs. "Deflated Sharpe" is the chance the
Sharpe is not just luck after counting how many settings were tried.
"""

import argparse
import ast
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "research"
AUDIT = RESEARCH / "audit_log.jsonl"
CARDS = RESEARCH / "cards"
CACHE = RESEARCH / "leaderboard_cache"
OUT = RESEARCH / "leaderboard.md"
THRESHOLD = 0.7
CODE_FILES = ["strategies/*.py", "indicators.py", "backtests/engine.py", "backtests/metrics.py", "backtests/sizing.py"]


@dataclass
class Run:
    strategy: str
    symbol: str
    settings: dict
    source: str  # "audit" or "card"
    where: str  # audit timestamp or card file name
    rules: list[tuple[str, str, bool]]  # (name, value shown, passed)
    dirty: bool = False  # audit only: the code differed from the commit when it ran
    standards_sha: str | None = None  # audit only
    trials: int | None = None
    span_end: str | None = None  # card only: last date of the data the run was judged on
    notes: list[str] = field(default_factory=list)

    @property
    def n_passed(self) -> int:
        return sum(p for _, _, p in self.rules)

    def value(self, rule: str) -> float | None:
        for name, shown, _ in self.rules:
            if name == rule:
                return number(shown)
        return None


def number(text: str) -> float | None:
    """'-14.2%' -> -14.2, '2.4x' -> 2.4, 'above 5x' -> 5.0, '405' -> 405.0; None if there is no number."""
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


# ---- reading the sources ----

def load_audit(path: Path) -> tuple[list[Run], int]:
    """(finished runs, number of crashed runs)."""
    runs, crashed = [], 0
    if not path.exists():
        return runs, crashed
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        res = rec.get("result", {})
        if "rules" not in res:
            crashed += 1
            continue
        runs.append(Run(rec["strategy"], rec["symbol"], rec.get("settings") or {}, "audit", rec["time"],
                        [(r["name"], r["value"], bool(r["passed"])) for r in res["rules"]],
                        dirty=bool(rec.get("dirty")), standards_sha=rec.get("standards_sha256"),
                        trials=(rec.get("trials") or {}).get("used")))
    return runs, crashed


HEADER = re.compile(r"^SCORECARD\s+(\S+) on (\S+) 5min\s+(.*?)\s*$")
RULE_LINE = re.compile(r"^(.*?)\s+(>=\s*\S+)\s+(PASS|FAIL)\s*$")
TRIALS = re.compile(r"after (\d+) trials")
SPAN = re.compile(r"^data (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})")
HOLDOUT_FALLBACK = "2026-04-22"


def parse_card(text: str, card: str) -> tuple[list[Run], int]:
    """Full SCORECARD blocks in a card (the judge's printed table). Returns (runs, blocks that couldn't be read).

    The table is fixed width: rule number in columns 0-2, rule name in columns 3-30, then value, needs, verdict.
    """
    runs, bad = [], 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        head = HEADER.match(line)
        if not head:
            continue
        name, symbol, raw = head.groups()
        try:
            settings = {} if raw.startswith("(default") else ast.literal_eval(raw)
            if not isinstance(settings, dict):
                raise ValueError(raw)
        except (ValueError, SyntaxError):
            bad += 1
            continue
        rules, trials, span_end = [], None, None
        for row in lines[i + 1:]:
            if row.startswith("OVERALL"):
                break
            if span := SPAN.match(row):
                span_end = span.group(2)
            if row.startswith("  ") and (t := TRIALS.search(row)):
                trials = int(t.group(1))
            if not re.match(r"^\d\s", row):
                continue
            m = RULE_LINE.match(row[31:])
            if m:
                rules.append((row[3:31].strip(), m.group(1).strip(), m.group(3) == "PASS"))
        if len(rules) != 8:
            bad += 1
            continue
        runs.append(Run(name, symbol, settings, "card", card, rules, trials=trials, span_end=span_end))
    return runs, bad


def load_cards(folder: Path) -> tuple[list[Run], int]:
    runs, bad = [], 0
    for path in sorted(folder.glob("*.md")):
        found, unreadable = parse_card(path.read_text(), path.name)
        runs += found
        bad += unreadable
    return runs, bad


# ---- duplicates, ranking, correlation ----

def canonical_key(run: Run) -> tuple:
    """(strategy, symbol, settings with the strategy's defaults filled in). Falls back to the raw settings."""
    try:
        from judge.scorecard import settings_key  # imported late: it pulls in optuna
        return run.strategy, run.symbol, settings_key(run.strategy, run.settings)
    except Exception:
        return run.strategy, run.symbol, json.dumps(run.settings, sort_keys=True, default=str)


def drop_duplicates(runs: list[Run], key=canonical_key) -> tuple[list[Run], list[tuple[Run, Run]]]:
    """Keep one run per key: audit over card, later over earlier (input order). Returns (kept, [(dropped, kept instead)])."""
    best: dict[tuple, Run] = {}
    dropped = []
    for run in runs:  # input order is oldest first
        k = key(run)
        old = best.get(k)
        if old is None:
            best[k] = run
        elif old.source == "audit" and run.source == "card":
            dropped.append((run, old))
        else:
            dropped.append((old, run))
            best[k] = run
    return list(best.values()), dropped


def rank(runs: list[Run]) -> list[Run]:
    """Most rules passed, then Deflated Sharpe, then Sharpe after costs (missing values last)."""
    def score(r: Run):
        return (r.n_passed, r.value("Deflated Sharpe") if r.value("Deflated Sharpe") is not None else -1.0,
                r.value("Sharpe after costs") if r.value("Sharpe after costs") is not None else -99.0)
    return sorted(runs, key=score, reverse=True)


def correlation(a: pd.Series, b: pd.Series) -> float | None:
    """Correlation of two daily-return series over the days both have; None if too short or one is flat."""
    both = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(both) < 20 or both.iloc[:, 0].std() == 0 or both.iloc[:, 1].std() == 0:
        return None
    return float(both.iloc[:, 0].corr(both.iloc[:, 1]))


def drop_correlated(ranked: list[Run], returns: dict[int, pd.Series | None], threshold: float = THRESHOLD):
    """Walk down the ranking; drop a run whose daily returns correlate above `threshold` with a run already kept.

    `returns` maps position in `ranked` to that run's daily returns (None = unavailable, the run stays).
    Returns (kept, [(dropped, kept run it copies, correlation)]).
    """
    kept: list[tuple[int, Run]] = []
    dropped = []
    for i, run in enumerate(ranked):
        series = returns.get(i)
        twin = None
        if series is not None:
            for j, other in kept:
                other_series = returns.get(j)
                c = correlation(series, other_series) if other_series is not None else None
                if c is not None and c > threshold:
                    twin = (other, c)
                    break
        if twin:
            dropped.append((run, twin[0], twin[1]))
        else:
            kept.append((i, run))
    return [r for _, r in kept], dropped


# ---- rebuilding daily returns ----

def code_fingerprint() -> str:
    h = hashlib.sha256()
    for pattern in CODE_FILES:
        for path in sorted(ROOT.glob(pattern)):
            h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()[:12]


def cache_path(run: Run, fingerprint: str) -> Path:
    key = json.dumps(canonical_key(run)) + fingerprint
    return CACHE / f"{run.strategy}_{run.symbol}_{hashlib.sha1(key.encode()).hexdigest()[:12]}.csv"


def daily_returns_of(strategy: str, symbol: str, settings: dict) -> pd.Series | None:
    """One backtest at the usual costs on the research cache, as daily returns. None if it can't be built."""
    try:
        from backtests.engine import STARTING_EQUITY, CostModel, run_backtest
        from backtests.metrics import daily_returns
        from judge.scorecard import bars_for, build
        res = run_backtest(bars_for(symbol), build(strategy, settings), costs=CostModel())
        return daily_returns(res.equity, STARTING_EQUITY)
    except Exception as exc:  # unknown strategy, a setting that no longer exists, ...
        print(f"  no daily returns for {strategy} {symbol} {settings}: {exc!r}", file=sys.stderr)
        return None


def load_returns(runs: list[Run]) -> dict[int, pd.Series | None]:
    """Daily returns per position in `runs`, from the cache or a fresh backtest (in parallel)."""
    fp = code_fingerprint()
    out: dict[int, pd.Series | None] = {}
    todo = {}
    for i, run in enumerate(runs):
        path = cache_path(run, fp)
        if path.exists():
            s = pd.read_csv(path, index_col=0, parse_dates=False).iloc[:, 0]
            out[i] = s
        else:
            todo[i] = path
    if todo:
        print(f"rebuilding daily returns for {len(todo)} run(s) ({len(out)} cached)...", file=sys.stderr)
        CACHE.mkdir(exist_ok=True)
        with ProcessPoolExecutor() as pool:
            futures = {i: pool.submit(daily_returns_of, runs[i].strategy, runs[i].symbol, runs[i].settings) for i in todo}
            for i, f in futures.items():
                s = f.result()
                out[i] = s
                if s is not None:
                    s.index = s.index.astype(str)
                    s.to_csv(todo[i], header=["return"])
    for s in out.values():
        if s is not None:
            s.index = s.index.astype(str)
    return out


# ---- the report ----

def fmt_settings(settings: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in settings.items()) or "defaults"


def fmt_value(run: Run, rule: str, suffix: str = "") -> str:
    for name, shown, _ in run.rules:
        if name == rule:
            return shown + suffix
    return "?"


def holdout_start() -> str:
    """First held-out session date, from the pipeline's config (the constant only, never the holdout bars)."""
    try:
        from data_pipeline.config import HOLDOUT_START
        return str(HOLDOUT_START)
    except Exception:
        return HOLDOUT_FALLBACK


def flags(run: Run, current_sha: str | None) -> str:
    out = []
    if run.source == "audit" and run.dirty:
        out.append("code was uncommitted")
    if run.source == "audit" and run.standards_sha and current_sha and run.standards_sha != current_sha:
        out.append("older standards")
    if run.source == "card":
        out.append("card only, run before the audit log")
        if run.span_end and run.span_end >= holdout_start():
            out.append(f"judged on data to {run.span_end}, which includes what is now the holdout")
    out += run.notes
    return "; ".join(out)


def render(kept: list[Run], dropped_dupes, dropped_corr, counts: dict, threshold: float, current_sha: str | None) -> str:
    L = ["# Leaderboard", "",
         "Paper trading only, educational use only. Nothing here is financial advice.", "",
         f"Generated by `uv run -m research.leaderboard` on {datetime.now(timezone.utc):%Y-%m-%d}. Do not edit by hand; rerun it.", "",
         "Every strategy run the judge has scored, best first. Ranked by rules passed (of 8), then Deflated Sharpe, then Sharpe "
         f"after costs. Exact duplicates are dropped, and so is any run whose daily profit line has a correlation above {threshold} "
         "with a better run already on the board (correlation: 1 = the two move together every day, 0 = unrelated).", "",
         f"- Runs read: {counts['audit']} from the audit log, {counts['card']} from cards. Crashed runs (not ranked): {counts['crashed']}. "
         f"Card blocks skipped as unreadable: {counts['unreadable']}.",
         f"- Exact duplicates dropped: {len(dropped_dupes)}. Dropped as look-alikes (correlation): {len(dropped_corr)}. On the board: {len(kept)}.",
         "- Runs that passed all 8 rules: " + (", ".join(f"{r.strategy} {r.symbol}" for r in kept if r.n_passed == 8) or "none") + ".",
         "", "## Ranking", "",
         "| # | Strategy | Symbol | Settings | Rules passed | Sharpe | Walk-forward Sharpe | Deflated Sharpe | Trades | Trials counted | Notes |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in enumerate(kept, 1):
        L.append(f"| {n} | {r.strategy} | {r.symbol} | {fmt_settings(r.settings)} | {r.n_passed} of {len(r.rules)} | "
                 f"{fmt_value(r, 'Sharpe after costs')} | {fmt_value(r, 'Walk-forward Sharpe')} | {fmt_value(r, 'Deflated Sharpe')} | "
                 f"{fmt_value(r, 'Trades')} | {r.trials if r.trials is not None else '?'} | {flags(r, current_sha)} |")
    if not kept:
        L.append("| | nothing scored yet | | | | | | | | | |")
    L += ["", "## Dropped", ""]
    if dropped_dupes or dropped_corr:
        L += ["| Strategy | Symbol | Settings | Why | Instead see |", "|---|---|---|---|---|"]
        for old, new in dropped_dupes:
            L.append(f"| {old.strategy} | {old.symbol} | {fmt_settings(old.settings)} | exact duplicate ({old.source} {old.where}) | "
                     f"{new.source} {new.where} |")
        for run, twin, c in dropped_corr:
            L.append(f"| {run.strategy} | {run.symbol} | {fmt_settings(run.settings)} | daily returns correlate {c:.2f} with a better run "
                     f"({run.n_passed} vs {twin.n_passed} rules passed) | {twin.strategy} {twin.symbol} {fmt_settings(twin.settings)} |")
    else:
        L.append("Nothing dropped.")
    L += ["", "## How to read this", "",
          "- A run that passed fewer than 8 rules is not a strategy to trade; the board shows what was scored, not what works.",
          "- Runs from cards were made before the audit log existed and under earlier versions of the judge (for example, the walk-forward "
          "rule once tuned a setting that is no longer in the search space). Their rule values are not strictly comparable with audit runs.",
          "- Cards that say the run saw data past the holdout date were judged before the last six months were locked away. Their numbers "
          "are not comparable with runs on the current 18-month cache, and they have seen data the final check should be blind to.",
          "- Correlation is measured on the current research cache (18 months), so it says how much two runs overlap, not how "
          "they would behave on new data. Card runs are rebuilt on that same cache, so their returns differ from what the card shows.",
          "- Daily returns are rebuilt by one backtest per run at the usual costs. A run whose returns could not be rebuilt (strategy "
          "or setting no longer exists) stays on the board unfiltered.",
          "- The research cache only; the holdout is never read."]
    return "\n".join(L) + "\n"


def build_board(threshold: float, use_returns: bool = True, audit: Path = AUDIT, cards: Path = CARDS):
    audit_runs, crashed = load_audit(audit)
    card_runs, unreadable = load_cards(cards)
    counts = {"audit": len(audit_runs), "card": len(card_runs), "crashed": crashed, "unreadable": unreadable}
    unique, dupes = drop_duplicates(card_runs + audit_runs)  # oldest first, audit last so it wins ties
    ranked = rank(unique)
    if use_returns:
        returns = load_returns(ranked)
        for i, run in enumerate(ranked):
            if returns.get(i) is None:
                run.notes.append("daily returns unavailable, not checked for look-alikes")
    else:
        returns = {}
    kept, corr = drop_correlated(ranked, returns, threshold)
    return kept, dupes, corr, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="drop runs correlated above this with a better one (default 0.7)")
    ap.add_argument("--no-returns", action="store_true", help="skip the backtests; only exact duplicates are removed")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    from judge.scorecard import load_standards
    current_sha = hashlib.sha256(json.dumps(load_standards(), sort_keys=True, default=str).encode()).hexdigest()
    kept, dupes, corr, counts = build_board(args.threshold, not args.no_returns)
    args.out.write_text(render(kept, dupes, corr, counts, args.threshold, current_sha))
    print(f"wrote {args.out}: {len(kept)} on the board, {len(dupes)} duplicates and {len(corr)} look-alikes dropped")


if __name__ == "__main__":
    main()
