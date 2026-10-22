"""Tests for the research_gate Stop hook (.claude/hooks/research_gate.py).

Runs the real script as a subprocess against a throwaway project directory.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "research_gate.py"

GOAL = """# Goal

Some prose.

## Required files
- `research/notes/a.md`
- research/notes/b.md

## Other
- research/notes/not_required.md
"""


def run_hook(root: Path, stop_hook_active: bool = False, stdin: str | None = None):
    payload = stdin if stdin is not None else json.dumps({"stop_hook_active": stop_hook_active})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".claude").mkdir()
    goals = tmp_path / "research" / "goals"
    goals.mkdir(parents=True)
    (goals / "tiny.md").write_text(GOAL)
    (tmp_path / "research" / "notes").mkdir()
    return tmp_path


def activate(root: Path, name: str = "research/goals/tiny.md"):
    (root / "research" / "goals" / "ACTIVE").write_text(name + "\n")


def counter(root: Path) -> Path:
    return root / ".claude" / "research_gate.count"


def test_no_active_does_nothing(project):
    result = run_hook(project)
    assert result.returncode == 0
    assert result.stderr == ""


def test_no_active_clears_stale_counter(project):
    counter(project).write_text("3")
    assert run_hook(project, stop_hook_active=True).returncode == 0
    assert not counter(project).exists()


def test_blocks_and_names_missing_files(project):
    activate(project)
    (project / "research/notes/a.md").write_text("x")
    result = run_hook(project)
    assert result.returncode == 2
    assert "research/notes/b.md" in result.stderr
    assert "research/notes/a.md" not in result.stderr
    assert "not_required" not in result.stderr


def test_allows_when_all_files_exist(project):
    activate(project)
    for n in ("a", "b"):
        (project / f"research/notes/{n}.md").write_text("x")
    counter(project).write_text("2")
    assert run_hook(project, stop_hook_active=True).returncode == 0
    assert not counter(project).exists()


def test_goal_name_can_be_bare_file_name(project):
    activate(project, "tiny.md")
    assert run_hook(project).returncode == 2


def test_counter_counts_blocks_in_a_row_then_gives_up(project):
    activate(project)
    for expected in range(1, 6):
        result = run_hook(project, stop_hook_active=True)
        assert result.returncode == 2
        assert counter(project).read_text() == str(expected)
    sixth = run_hook(project, stop_hook_active=True)
    assert sixth.returncode == 0
    assert "giving up" in sixth.stderr
    assert not counter(project).exists()


def test_fresh_stop_resets_the_counter(project):
    activate(project)
    counter(project).write_text("5")
    result = run_hook(project, stop_hook_active=False)
    assert result.returncode == 2
    assert counter(project).read_text() == "1"


@pytest.mark.parametrize("name", ["missing.md", ""])
def test_bad_active_is_a_non_blocking_error(project, name):
    activate(project, name)
    result = run_hook(project)
    assert result.returncode == 1
    assert "research_gate" in result.stderr


def test_goal_without_required_files_section_is_a_non_blocking_error(project):
    (project / "research/goals/tiny.md").write_text("# Goal\n\nNo list here.\n")
    activate(project)
    assert run_hook(project).returncode == 1


def test_bad_stdin_still_works(project):
    activate(project)
    assert run_hook(project, stdin="not json").returncode == 2
