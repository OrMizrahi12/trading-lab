#!/usr/bin/env bash
# Run one fresh `claude -p` session per untested idea in research/journal.md.
#
# Paper trading only, educational use only. Nothing here is financial advice.
#
# An idea is untested when its Status cell in the journal table starts with "not started".
# Ideas run in table order (the table is ranked by score). An idea counts as done when
# research/batch_logs/<id>.json exists: that file is only written when the session ended
# with subtype "success" and is_error false. Rerun the script after a usage-limit reset and
# it picks up where it stopped.
#
# Per idea, in research/batch_logs/ (gitignored):
#   <id>.log         header, command, stderr, result summary (appended on every attempt)
#   <id>.json        raw `--output-format json` result of the successful attempt (the done marker)
#   <id>.error.json  raw output of the last failed attempt
#
# Exit codes: 0 finished (all done, or time budget used), 1 setup error, 75 stopped on the
# usage limit (rerun later), 130 interrupted.
#
# Usage: scripts/research_batch.sh [options]
#   --dry-run            show the plan and the commands; call nothing, write nothing
#   --max-turns N        turn cap for each session (default 40)
#   --budget-minutes N   total wall-clock budget for the whole run (default 240)
#   --max-ideas N        run at most N ideas this time (default: all untested)
#   --only ID            run just this journal id (e.g. H3), even if it has a done marker
#   --plan-only          sessions may only read files and must answer with a test plan:
#                        Bash, Edit and Write are blocked, nothing is committed
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$ROOT/research/journal.md"
LOGDIR="$ROOT/research/batch_logs"

DRY_RUN=0
PLAN_ONLY=0
MAX_TURNS=40
BUDGET_MIN=240
MAX_IDEAS=0
ONLY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --plan-only) PLAN_ONLY=1 ;;
    --max-turns) MAX_TURNS="${2:?--max-turns needs a number}"; shift ;;
    --budget-minutes) BUDGET_MIN="${2:?--budget-minutes needs a number}"; shift ;;
    --max-ideas) MAX_IDEAS="${2:?--max-ideas needs a number}"; shift ;;
    --only) ONLY="${2:?--only needs a journal id}"; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
  shift
done

for n in MAX_TURNS BUDGET_MIN MAX_IDEAS; do
  [[ "${!n}" =~ ^[0-9]+$ ]] || { echo "$n must be a whole number" >&2; exit 1; }
done
[[ -f "$JOURNAL" ]] || { echo "missing $JOURNAL" >&2; exit 1; }
command -v claude >/dev/null || { echo "claude CLI not found" >&2; exit 1; }

# --- journal parsing: "id<TAB>status<TAB>score<TAB>title<TAB>raw row" per table row ---------------------
list_ideas() {
  python3 -I - "$JOURNAL" <<'PY'
import re, sys
for line in open(sys.argv[1], encoding="utf-8"):
    if not line.startswith("|"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    # table columns: # | Idea | Why | Source | Test | Promise | Cheap | Score | Status | Result
    if len(cells) < 9 or cells[0] in ("#", "") or set(cells[0]) <= set("-: "):
        continue
    bold = re.match(r"\*\*(.+?)\*\*", cells[1])
    title = re.sub(r"`|\s*\(own hypothesis\)", "", bold.group(1) if bold else cells[1]).strip()
    print("\t".join([cells[0], cells[8], re.sub(r"\*", "", cells[7]), title, line.strip()]))
PY
}

# --- prompt for one idea ---------------------------------------------------------------------
build_prompt() {
  local id="$1" title="$2" row="$3"
  if [[ $PLAN_ONLY -eq 1 ]]; then
    cat <<EOF
Journal idea $id ("$title"). Its row of research/journal.md is pasted below; you have very few turns, so do not read any files, everything you need is here.
Your whole job: write a TEST PLAN as your final answer. Do not write code, do not edit or create files, do not run commands.
The plan covers: the exact question; the data needed and whether the cache already has it; the settings to freeze BEFORE running (and why those); what to build, file by file; the judge runs (uv run -m judge.scorecard ...) with the --trials count and why; the PASS and FAIL conditions fixed in advance; the risks (look-ahead, overfitting, thin data). Explain each trading term in plain English the first time you use it. Keep it under 400 words. Paper trading only.

Journal row (columns: id | idea | why it might work | source | how we would test it | promise | cheap | score | status | result):
$row
EOF
  else
    cat <<EOF
Work journal idea $id ("$title") in research/journal.md, following the lab's research loop. Read that row, AGENTS.md and the cards in research/cards/ first.
1. Write down the question, the frozen settings and the PASS and FAIL conditions BEFORE running anything. Count every variant you try as a trial for the Deflated Sharpe.
2. Do the cheapest test the row describes. Only build a strategy if the cheap check says it is worth it. Add tests for new code (uv run pytest).
3. Judge it with uv run -m judge.scorecard. The printed PASS/FAIL lines decide, not your opinion. Do not tune after seeing results; a failed idea is a valid finish.
4. Write the result card research/cards/<id>_<short_name>.md and update this idea's Status and Result cells and the Log in research/journal.md.
5. Commit straight to main (no branches, no pull requests).
Limits: paper trading only, never connect to anything but IB Gateway paper on port 4002, no real orders, no credentials. If the idea needs IB Gateway and it is not reachable, record that on the card, set Status to "blocked" and stop; do not work around it. Explain each trading term in plain English the first time you use it.
EOF
  fi
}

# --- one claude -p command, as an argv array in CLAUDE_CMD -----------------------------------
build_cmd() {
  CLAUDE_CMD=(claude -p "$1" --output-format json --max-turns "$MAX_TURNS")
  if [[ $PLAN_ONLY -eq 1 ]]; then
    CLAUDE_CMD+=(--disallowedTools "Bash Edit Write NotebookEdit")
  else
    CLAUDE_CMD+=(--permission-mode acceptEdits --allowedTools "Bash(uv run:*)" "Bash(git add:*)" "Bash(git commit:*)" "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)")
  fi
}

# --- pick the ideas ----------------------------------------------------------------------------
declare -a IDS=() TITLES=() SCORES=() ROWS=()
declare -a SKIPPED=()
while IFS=$'\t' read -r id status score title row; do
  if [[ -n "$ONLY" ]]; then
    [[ "$id" == "$ONLY" ]] || continue
  elif [[ "${status,,}" != "not started"* ]]; then
    SKIPPED+=("$id: status \"$status\"")
    continue
  elif [[ -f "$LOGDIR/$id.json" ]]; then
    SKIPPED+=("$id: done marker research/batch_logs/$id.json")
    continue
  fi
  IDS+=("$id"); TITLES+=("$title"); SCORES+=("$score"); ROWS+=("$row")
done < <(list_ideas)

if [[ -n "$ONLY" && ${#IDS[@]} -eq 0 ]]; then
  echo "no journal row with id $ONLY" >&2; exit 1
fi
if [[ $MAX_IDEAS -gt 0 && ${#IDS[@]} -gt $MAX_IDEAS ]]; then
  IDS=("${IDS[@]:0:$MAX_IDEAS}"); TITLES=("${TITLES[@]:0:$MAX_IDEAS}"); SCORES=("${SCORES[@]:0:$MAX_IDEAS}"); ROWS=("${ROWS[@]:0:$MAX_IDEAS}")
fi

mode="full research loop"; [[ $PLAN_ONLY -eq 1 ]] && mode="plan only (Bash/Edit/Write blocked)"
echo "research_batch: mode=$mode max-turns=$MAX_TURNS budget=${BUDGET_MIN}min ideas=${#IDS[@]}"
for s in "${SKIPPED[@]}"; do echo "  skip  $s"; done

if [[ $DRY_RUN -eq 1 ]]; then
  echo "DRY RUN: nothing is called, nothing is written."
  for i in "${!IDS[@]}"; do
    build_cmd "$(build_prompt "${IDS[$i]}" "${TITLES[$i]}" "${ROWS[$i]}")"
    printf '  would run  %-3s score %-3s %s\n' "${IDS[$i]}" "${SCORES[$i]}" "${TITLES[$i]}"
    printf '             log: research/batch_logs/%s.log  done marker: research/batch_logs/%s.json\n' "${IDS[$i]}" "${IDS[$i]}"
    printf '             cmd: claude -p <prompt %d chars>' "$(build_prompt "${IDS[$i]}" "${TITLES[$i]}" "${ROWS[$i]}" | wc -c)"
    printf ' %s\n' "${CLAUDE_CMD[*]:3}"
  done
  exit 0
fi

[[ ${#IDS[@]} -gt 0 ]] || { echo "nothing to do."; exit 0; }

# --- run ---------------------------------------------------------------------------------------
mkdir -p "$LOGDIR"
exec 9>"$LOGDIR/.lock"
flock -n 9 || { echo "another research_batch.sh is already running" >&2; exit 1; }

# Matches the usage-limit / rate-limit messages. Only checked when a session failed.
LIMIT_RE='usage limit|limit reached|rate.?limit|hit your limit|limit will reset|resets? (at|in)|overloaded_error|quota'

DEADLINE=$(( $(date +%s) + BUDGET_MIN * 60 ))
trap 'echo "interrupted"; exit 130' INT TERM
cd "$ROOT"
ran=0

for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; title="${TITLES[$i]}"
  remaining=$(( DEADLINE - $(date +%s) ))
  if (( remaining <= 0 )); then
    echo "time budget (${BUDGET_MIN} min) used up; stopping before $id. Rerun to continue."
    exit 0
  fi

  log="$LOGDIR/$id.log"; tmp="$LOGDIR/$id.out.tmp"
  build_cmd "$(build_prompt "$id" "$title" "${ROWS[$i]}")"
  {
    echo "=== $(date -u +%FT%TZ) idea $id: $title (mode: $mode, max-turns $MAX_TURNS, ${remaining}s left in budget)"
    echo "cmd: claude -p <prompt> ${CLAUDE_CMD[*]:3}"
  } >> "$log"
  echo "[$id] $title ..."

  set +e
  timeout "$remaining" "${CLAUDE_CMD[@]}" < /dev/null > "$tmp" 2>> "$log"
  rc=$?
  set -e

  # One line of facts from the JSON: subtype, is_error, turns, cost, seconds; then the result text.
  summary="$(python3 -I - "$tmp" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print(f"unparseable output ({type(e).__name__})"); sys.exit(0)
print(f"subtype={d.get('subtype')} is_error={d.get('is_error')} terminal_reason={d.get('terminal_reason')} "
      f"turns={d.get('num_turns')} cost_usd={d.get('total_cost_usd')} seconds={round((d.get('duration_ms') or 0)/1000)}")
print(d.get("result") or "\n".join(d.get("errors") or []) or "(no result text)")
PY
)"
  printf 'exit=%s\n%s\n' "$rc" "$summary" >> "$log"

  if (( rc == 0 )) && grep -q '"subtype":"success"' "$tmp" && grep -q '"is_error":false' "$tmp"; then
    mv "$tmp" "$LOGDIR/$id.json"
    echo "[$id] done -> research/batch_logs/$id.json"
    ran=$((ran + 1))
    continue
  fi

  mv "$tmp" "$LOGDIR/$id.error.json"
  if (( rc == 124 )); then
    echo "[$id] ran out of time budget; stopping. Rerun to retry $id."
    echo "stopped: time budget" >> "$log"
    exit 0
  fi
  if grep -qiE "$LIMIT_RE" "$LOGDIR/$id.error.json" "$log"; then
    echo "[$id] usage limit hit; stopping cleanly. $ran idea(s) finished this run. Rerun after the reset; finished ideas are skipped."
    echo "stopped: usage limit" >> "$log"
    exit 75
  fi
  echo "[$id] FAILED (exit $rc): $(head -1 <<<"$summary"). Continuing; see research/batch_logs/$id.log"
done

echo "research_batch: finished, $ran idea(s) completed this run."
