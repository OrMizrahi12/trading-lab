#!/usr/bin/env python3
"""PreToolUse hook: lock the judge and the holdout data.

An agent told to "find profit" can reach it by loosening the judge instead of
finding an edge (Goodhart's law). So this hook blocks:

- changing the judge: anything under judge/, plus the files that hold this lock
  (.claude/settings.json, .claude/settings.local.json, this hook);
- touching the holdout: data/holdout/ is the last six months of bars, kept out
  of the cache so research never sees them. Reading, listing or writing it is blocked;
- running the scorecard against another standards file (`--standards`).

Reading the judge is fine (cat, grep, git diff, `uv run -m judge.scorecard`).
A Bash command that mentions a locked path may only use read-only commands on it.

This is a guardrail against drift, not a sandbox: it matches paths written in the
command, so a path built at run time (string concatenation, a variable) gets past it.
A human can still edit these files by hand outside Claude Code.

Exit code 2 blocks the tool call and shows stderr to Claude. Bad input also blocks.
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

LOCKED_JUDGE = ("judge", ".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/judge_guard.py")
LOCKED_HOLDOUT = "data/holdout"
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}

JUDGE_REF = re.compile(
    r"(?<![\w.-])judge(?=[/\s'\"]|$)|\.claude/(?:settings(?:\.local)?\.json|hooks/judge_guard\.py)"
)
# data/holdout, data', 'holdout and Path("data")/"holdout" all match
HOLDOUT_CODE = re.compile(r"""data['"\s)]*[/,]\s*['"]?holdout""", re.I)
READ_ONLY = {"cat", "head", "tail", "less", "more", "ls", "grep", "egrep", "rg", "wc", "diff",
             "stat", "file", "nl", "md5sum", "sha256sum"}
GIT_SAFE = {"diff", "log", "show", "status", "blame", "ls-files", "check-ignore", "add", "commit"}
WRAPPERS = {"sudo", "env", "command", "time", "nice"}

JUDGE_MSG = (
    "Blocked by judge_guard: {what} would change the judge ({target}). The judge and the lock "
    "that protects it are off limits to the agent so the standards can't be loosened to make a "
    "strategy pass. You can read them. If a change is really needed (for example registering a "
    "new strategy), ask the user to make it by hand."
)
HOLDOUT_MSG = (
    "Blocked by judge_guard: {what} would touch data/holdout/ ({target}). That is the last six "
    "months of QQQ and SPY bars, kept out of the research cache so no idea is tuned on it. "
    "Research uses data/cache/ only. Ask the user if a holdout run is due."
)


def block(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def resolve(path: str, cwd: Path) -> Path:
    return Path(os.path.realpath(cwd / path))  # absolute paths ignore cwd; follows symlinks and ..


def under(p: Path, root: Path, rel: str) -> bool:
    target = Path(os.path.realpath(root / rel))
    return p == target or target in p.parents


def strip_commit_message(cmd: str) -> str:
    """Drop `git commit` message text: it may name a locked path without touching it."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return cmd
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", "", cmd, flags=re.S)  # heredoc body
    return re.sub(r"""(?:-m|--message)(?:=|\s+)(?:"(?:[^"\\]|\\.)*"|'[^']*')""", "", cmd)


def holdout_in_text(text: str) -> str | None:
    """The offending path-like string if `text` points into data/holdout, else None."""
    m = HOLDOUT_CODE.search(text)
    if m:
        return m.group(0)
    for tok in re.split(r"""[\s'"`;|&()<>=:,]+""", text):
        if "/" not in tok:  # a bare word "holdout" is just a word
            continue
        comps = [c for c in tok.split("/") if c]
        for i, c in enumerate(comps):
            if c.lower() == "holdout":
                return tok
            # data/ho*, data/*, data/**: a glob that would match the folder
            if i and comps[i - 1] == "data" and any(ch in c for ch in "*?[") and fnmatch.fnmatchcase("holdout", c):
                return tok
    return None


def judge_ref_allowed(segment: str) -> bool:
    """True if a command segment that names a locked path only reads it."""
    seg = re.sub(r"\d?>+\s*&?\s*(?:/dev/null|\d)\b", "", segment)  # 2>/dev/null, 2>&1 are harmless
    if ">" in seg:
        return False
    words = seg.split()
    while words and (words[0] in WRAPPERS or re.match(r"\w+=", words[0])):  # sudo, FOO=bar
        words.pop(0)
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    if not words:
        return False
    if words[0] == "git":
        sub = next((w for w in words[1:] if not w.startswith("-")), "")
        return sub in GIT_SAFE
    return words[0] in READ_ONLY


def check_bash(cmd: str) -> int:
    text = strip_commit_message(cmd)
    hit = holdout_in_text(text)
    if hit:
        return block(HOLDOUT_MSG.format(what="this command", target=hit))
    if "judge.scorecard" in text and re.search(r"--standards\b", text):
        return block(JUDGE_MSG.format(what="running the scorecard against another standards file",
                                      target="--standards"))
    for seg in re.split(r"[;&|\n]+|\$\(|`|\(|\)", text):
        m = JUDGE_REF.search(seg)
        if m and not judge_ref_allowed(seg):
            return block(JUDGE_MSG.format(what="this command", target=m.group(0).strip("'\" ")))
    return 0


def check_path(tool: str, raw: str, root: Path, cwd: Path) -> int:
    p = resolve(raw, cwd)
    if under(p, root, LOCKED_HOLDOUT):
        return block(HOLDOUT_MSG.format(what=f"{tool} on this path", target=raw))
    if tool in EDIT_TOOLS and any(under(p, root, rel) for rel in LOCKED_JUDGE):
        return block(JUDGE_MSG.format(what=f"{tool} on this file", target=raw))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        args = payload.get("tool_input") or {}
        root = Path(os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()))
        cwd = Path(payload.get("cwd") or root)
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        return block(f"Blocked by judge_guard: could not read the hook input ({exc}).")

    if tool == "Bash":
        return check_bash(str(args.get("command", "")))
    if tool in EDIT_TOOLS:
        return check_path(tool, str(args.get(EDIT_TOOLS[tool], "")), root, cwd)
    if tool in ("Read", "Grep", "Glob"):
        raw = str(args.get("file_path") or args.get("path") or "")
        if raw and check_path(tool, raw, root, cwd):
            return 2
        # Grep/Glob can reach the folder through a glob over a parent dir
        pattern = str((args.get("glob") if tool == "Grep" else args.get("pattern")) or "")
        hit = holdout_in_text(f"{raw}/{pattern}" if raw else pattern)
        if hit:
            return block(HOLDOUT_MSG.format(what=f"{tool} with this pattern", target=hit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
