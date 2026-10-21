#!/usr/bin/env python3
"""Stop hook: refuse to finish while the git working tree has uncommitted changes.

Exit code 2 blocks the stop and feeds stderr back to Claude.
"""
import json
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=payload.get("cwd") or None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"require_commit: git status failed: {result.stderr.strip()}", file=sys.stderr)
        return 1  # non-blocking error

    if result.stdout.strip():
        print("Commit and push your work before you stop.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
