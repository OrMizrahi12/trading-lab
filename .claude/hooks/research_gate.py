#!/usr/bin/env python3
"""Stop hook: keep Claude working until a research goal's files exist.

Does nothing unless research/goals/ACTIVE exists. ACTIVE holds the name of a goal
file (a path from the project root, or a file name inside research/goals/). The goal
file's done-condition is a "## Required files" section: a bullet list of paths
(backticks optional), relative to the project root. Example:

    ## Required files
    - `research/notes/a.md`
    - research/notes/b.md

If any are missing, exit code 2 blocks the stop and feeds the missing paths to Claude.

Loop guard: stop_hook_active is true when Claude is already continuing because a Stop
hook blocked it. Then the counter in .claude/research_gate.count keeps going up; at
MAX_BLOCKS blocks in a row the hook gives up and lets Claude stop. A stop that is not
a continuation (stop_hook_active false), a passing gate or a missing ACTIVE resets it.
"""
import json
import os
import re
import sys
from pathlib import Path

MAX_BLOCKS = 5
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
BULLET = re.compile(r"^\s*[-*]\s+`?([^`\s]+)`?\s*$")


def project_root(payload: dict) -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    return Path(root) if root else Path(__file__).resolve().parents[2]


def required_files(goal_text: str) -> list[str]:
    """Bullet paths under the '## Required files' heading, up to the next heading."""
    paths: list[str] = []
    in_section = False
    for line in goal_text.splitlines():
        heading = HEADING.match(line)
        if heading:
            in_section = heading.group(1).lower() == "required files"
        elif in_section and (bullet := BULLET.match(line)):
            paths.append(bullet.group(1))
    return paths


def read_count(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}

    root = project_root(payload)
    active = root / "research" / "goals" / "ACTIVE"
    counter = root / ".claude" / "research_gate.count"

    if not active.is_file():
        counter.unlink(missing_ok=True)
        return 0

    # A fresh stop (not a continuation after one of our blocks) starts a new chain.
    if not payload.get("stop_hook_active"):
        counter.unlink(missing_ok=True)

    name = active.read_text().strip()
    goal = root / name
    if not goal.is_file():
        goal = root / "research" / "goals" / name
    if not name or not goal.is_file():
        print(f"research_gate: ACTIVE names {name!r}, which is not a goal file.", file=sys.stderr)
        return 1  # non-blocking error

    wanted = required_files(goal.read_text())
    if not wanted:
        print(f"research_gate: {goal.name} has no '## Required files' list.", file=sys.stderr)
        return 1  # non-blocking error

    missing = [p for p in wanted if not (root / p).is_file()]
    if not missing:
        counter.unlink(missing_ok=True)
        return 0

    blocks = read_count(counter)
    if blocks >= MAX_BLOCKS:
        counter.unlink(missing_ok=True)
        print(
            f"research_gate: giving up after {MAX_BLOCKS} blocks in a row; still missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 0

    counter.write_text(str(blocks + 1))
    print(
        f"Goal {goal.name} is not done (block {blocks + 1} of {MAX_BLOCKS}). "
        "Create these missing files before you stop:\n"
        + "\n".join(f"- {p}" for p in missing),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
