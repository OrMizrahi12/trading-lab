"""Tests for the judge_guard PreToolUse hook (.claude/hooks/judge_guard.py).

Runs the real script as a subprocess against a throwaway project directory.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "judge_guard.py"


@pytest.fixture
def root(tmp_path):
    for d in ("judge", "data/holdout", "data/cache", ".claude/hooks"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "judge" / "standards.yaml").write_text("min_trades: 200\n")
    return tmp_path


def run(root: Path, tool: str, stdin: str | None = None, **tool_input):
    payload = stdin if stdin is not None else json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True,
                          env={"CLAUDE_PROJECT_DIR": str(root)})


def bash(root, command):
    return run(root, "Bash", command=command)


def assert_blocked(r, word):
    assert r.returncode == 2, r.stderr
    assert "judge_guard" in r.stderr and word in r.stderr


# --- the judge: Edit / Write ------------------------------------------------

@pytest.mark.parametrize("tool", ["Edit", "Write"])
@pytest.mark.parametrize("rel", ["judge/standards.yaml", "judge/scorecard.py", "judge/new_rule.py",
                                 ".claude/settings.json", ".claude/settings.local.json",
                                 ".claude/hooks/judge_guard.py", "research/trials.csv",
                                 "research/audit_log.jsonl"])
def test_edit_or_write_of_the_judge_is_blocked(root, tool, rel):
    assert_blocked(run(root, tool, file_path=str(root / rel)), "judge")


def test_relative_path_and_dotdot_are_resolved(root):
    assert_blocked(run(root, "Edit", file_path="strategies/../judge/standards.yaml"), "judge")


def test_symlink_into_the_judge_is_followed(root):
    (root / "link.yaml").symlink_to(root / "judge" / "standards.yaml")
    assert_blocked(run(root, "Write", file_path=str(root / "link.yaml")), "judge")


def test_notebook_edit_of_the_judge_is_blocked(root):
    assert_blocked(run(root, "NotebookEdit", notebook_path=str(root / "judge" / "x.ipynb")), "judge")


@pytest.mark.parametrize("rel", ["strategies/orb.py", "research/journal.md", "tests/test_judge_guard.py",
                                 "judge_notes.md", ".claude/hooks/research_gate.py"])
def test_editing_other_files_is_allowed(root, rel):
    assert run(root, "Edit", file_path=str(root / rel)).returncode == 0


def test_reading_the_judge_is_allowed(root):
    assert run(root, "Read", file_path=str(root / "judge" / "standards.yaml")).returncode == 0


# --- the judge: Bash --------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "sed -i 's/200/20/' judge/standards.yaml",
    "echo 'min_trades: 1' > judge/standards.yaml",
    "echo x >> ./judge/standards.yaml",
    "cat /tmp/x | tee judge/standards.yaml",
    "cp /tmp/weak.yaml judge/standards.yaml",
    "mv judge/scorecard.py /tmp/",
    "rm -r judge",
    "chmod u+w judge/standards.yaml",
    "git checkout -- judge/standards.yaml",
    "git restore --source=HEAD~3 judge/",
    "uv run python -c \"open('judge/standards.yaml','w').write('')\"",
    "python3 - <<'EOF'\nfrom pathlib import Path\nPath('judge/standards.yaml').write_text('')\nEOF",
    "cd judge && sed -i s/a/b/ standards.yaml",
    "ln -s /tmp/x judge/scorecard.py",
    "sed -i 's/pytest/x/' .claude/settings.json",
    "rm .claude/hooks/judge_guard.py",
    "cat judge/standards.yaml && cp /tmp/a judge/standards.yaml",
    "uv run -m judge.scorecard orb --standards /tmp/weak.yaml",
    # the trial ledger and audit log are append-only records written by the judge itself
    "sed -i '$d' research/trials.csv",
    "echo 'x' > research/trials.csv",
    "truncate -s 0 research/audit_log.jsonl",
    "rm research/trials.csv",
    "grep -v orb research/trials.csv | tee research/trials.csv",
    # quoting must not hide a write: command substitution runs inside double quotes
    "echo \"$(cp /tmp/a judge/standards.yaml)\"",
    "echo \"`cp /tmp/a judge/standards.yaml`\"",
    "cat 'a;b' && cp /tmp/a judge/standards.yaml",
    "grep \"a\\|b\" judge/standards.yaml > judge/other.yaml",
])
def test_bash_that_changes_the_judge_is_blocked(root, cmd):
    r = bash(root, cmd)
    assert r.returncode == 2 and "judge_guard" in r.stderr, cmd


@pytest.mark.parametrize("cmd", [
    "cat judge/standards.yaml",
    "grep -n min_trades judge/standards.yaml",
    "ls -la judge/ 2>/dev/null",
    "diff judge/standards.yaml /tmp/other.yaml",
    "git diff judge/standards.yaml",
    "git log --oneline -- judge/",
    "git add judge/standards.yaml .claude/settings.json",
    "uv run -m judge.scorecard opening_range_breakout --symbol QQQ --trials 42",
    "uv run pytest tests/test_scorecard.py",
    # a separator inside quotes (a regex alternation) is not a separator
    "grep -n \"min_\\|threshold\\|trades\" judge/standards.yaml",
    "grep -E \"min_|max_\" judge/standards.yaml",
    "grep -n 'a;b' judge/standards.yaml",
    "grep -c '(a)' judge/scorecard.py",
    # the records can be read, tailed and committed
    "cat research/trials.csv",
    "tail -n 5 research/audit_log.jsonl",
    "wc -l research/trials.csv",
    "git add research/trials.csv research/audit_log.jsonl",
    "git commit -m \"judge: tighten judge/standards.yaml thresholds\"",
    "git commit -m \"$(cat <<'EOF'\nresearch: edits judge/standards.yaml? no\n\nsee data/holdout/\nEOF\n)\"",
])
def test_bash_that_only_reads_the_judge_is_allowed(root, cmd):
    r = bash(root, cmd)
    assert r.returncode == 0, (cmd, r.stderr)


def test_commit_message_exemption_does_not_hide_a_chained_write(root):
    assert_blocked(bash(root, "git commit -m 'x' && cp /tmp/a judge/standards.yaml"), "judge")


# --- the holdout ------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "cat data/holdout/QQQ_5min.parquet",
    "ls data/holdout",
    "uv run python -c \"import pandas as pd; pd.read_parquet('data/holdout/QQQ_5min.parquet')\"",
    "uv run python -c \"from pathlib import Path; Path('data')/'holdout'\"",
    "uv run python -c \"import os; os.path.join('data', 'holdout', 'SPY_5min.parquet')\"",
    "cp data/holdout/SPY_5min.parquet data/cache/",
    "cd data && cat holdout/SPY_5min.parquet",
    "cat data/ho*/QQQ_5min.parquet",
    "cat data/h?ldout/QQQ_5min.parquet",
    "ls data/*",
    "cat /home/agent/trading-lab/data/holdout/QQQ_5min.parquet",
    "grep -r close data/holdout/",
])
def test_bash_that_touches_the_holdout_is_blocked(root, cmd):
    assert_blocked(bash(root, cmd), "holdout")


@pytest.mark.parametrize("cmd", [
    "grep -n holdout backtests/split_validation.py",
    "uv run -m backtests.split_validation",
    "ls data/cache",
    "cat data/AAPL_5min_TRADES.csv",
    "uv run pytest tests/test_judge_guard.py",
])
def test_bash_that_leaves_the_holdout_alone_is_allowed(root, cmd):
    assert bash(root, cmd).returncode == 0


@pytest.mark.parametrize("tool,args", [
    ("Read", {"file_path": "data/holdout/QQQ_5min.parquet"}),
    ("Write", {"file_path": "data/holdout/new.parquet"}),
    ("Edit", {"file_path": "data/holdout/QQQ_5min.parquet"}),
    ("Grep", {"pattern": "x", "path": "data/holdout"}),
    ("Grep", {"pattern": "x", "path": "data", "glob": "*/*.parquet"}),
    ("Glob", {"pattern": "data/holdout/*.parquet"}),
    ("Glob", {"pattern": "*/*.parquet", "path": "data"}),
])
def test_other_tools_on_the_holdout_are_blocked(root, tool, args):
    assert_blocked(run(root, tool, **args), "holdout")


def test_reading_the_cache_is_allowed(root):
    assert run(root, "Read", file_path=str(root / "data" / "cache" / "QQQ_5min.parquet")).returncode == 0
    assert run(root, "Glob", pattern="data/cache/*.parquet").returncode == 0


# --- plumbing ---------------------------------------------------------------

@pytest.mark.parametrize("stdin", ["not json", "", "[1, 2]"])
def test_unreadable_input_blocks(root, stdin):
    r = run(root, "Bash", stdin=stdin)
    assert r.returncode == 2 and "judge_guard" in r.stderr


def test_unrelated_tools_pass(root):
    assert run(root, "WebSearch", query="judge").returncode == 0
