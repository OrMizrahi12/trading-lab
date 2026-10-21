#!/usr/bin/env python3
"""PreToolUse hook: block anything that mentions a live Interactive Brokers port.

7496 (TWS) and 4001 (IB Gateway) are the live, real-money ports. This repo is
paper trading only, so the paper ports (7497 and 4002) are the only ones allowed.

Exit code 2 blocks the tool call and shows stderr to Claude.
"""
import json
import re
import sys

LIVE_PORTS = {"7496": "TWS live", "4001": "IB Gateway live"}
PORT_RE = re.compile(r"(?<!\d)(7496|4001)(?!\d)")

# Which field of tool_input to scan, per tool.
FIELDS = {"Bash": ("command",), "Edit": ("new_string",), "Write": ("content",)}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"block_live_ports: could not parse hook input: {exc}", file=sys.stderr)
        return 1  # non-blocking error

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    text = "\n".join(str(tool_input.get(f, "")) for f in FIELDS.get(tool, ()))

    found = sorted({m.group(1) for m in PORT_RE.finditer(text)})
    if not found:
        return 0

    names = ", ".join(f"{p} ({LIVE_PORTS[p]})" for p in found)
    print(
        f"Blocked: this {tool} call mentions live Interactive Brokers port {names}. "
        "This repo is paper trading only. Use the paper ports instead: "
        "7497 (TWS paper) or 4002 (IB Gateway paper).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
