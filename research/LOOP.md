# The research loop

Paper trading only, educational use only. Nothing here is financial advice.

The one procedure every researcher follows (`.claude/agents/researcher-*.md`, `scripts/research_batch.sh`, or you by hand). One run = one journal idea, from the first line of the card to the committed journal row. Explain each trading term in plain English the first time you use it.

## Hard rules (a hook enforces most of these; do not look for ways around it)

- **Paper only.** No real orders, no live brokerage, no credentials. IB Gateway paper on port 4002 is the only connection allowed.
- **Locked files.** Never edit or read: `judge/` (read is fine, edit is not), `research/trials.csv`, `research/audit_log.jsonl`, `.claude/settings*.json`, `.claude/hooks/judge_guard.py`, `data/holdout/`. Never pass `--standards`. If a rule, threshold or `STRATEGIES` entry must change, that is the user's decision: ask, do not work around it.
- **Never change** `judge/standards.yaml`, `backtests/engine.py`, `CostModel`, `backtests/sizing.py`, `data/cache/`, or another strategy's behaviour (a new optional parameter must default to the old behaviour, with a test that proves a run without it is unchanged).
- **Do not tune after seeing a result.** A failed idea is a valid finish and gets a card like any other.
- Commit straight to `main`. No branches, no pull requests, no push.

## Budget

Your run ends at the first of: the turn limit you were given; **8 judge runs** (every distinct settings x symbol counts); **1 new strategy**; the idea's own stop condition. When the budget is gone, write the card with what you have, say "budget exhausted" in the journal Status, commit and stop. Do not ask for more.

## Steps

1. **Pick the idea.** If the prompt names an id, take it. Otherwise take the highest-scoring row of your family whose Status starts with "not started" (`research/families.txt` maps ids to families). Read its journal row, `AGENTS.md`, and the cards in `research/cards/` (what failed before and why).
2. **Pre-register.** Create `research/cards/<id>_<short_name>.md` with: the question in one sentence, the frozen settings and why, the PASS and FAIL conditions, the number of judge runs you will make and the `--trials` you will pass. Commit it **before** running anything. Pre-registering is what makes the result mean something.
3. **Cheap check first.** If the row says to measure something in pandas first (a statistic, a diagnostic), do that on `data/cache/` only. If the number says there is nothing there, the card records it, the verdict is "no strategy worth building", and you stop. That is a good outcome, not a failure.
4. **Spec.** Copy `strategies/specs/TEMPLATE.yaml` to `strategies/specs/<name>.yaml` and fill it in.
5. **Code with tests.** `strategies/<name>.py`: a pure function `on_bar(bars, position) -> Entry | Exit | None` as in `strategies/base.py`; long-only; symbol-agnostic; costs, sizing and fills belong to the engine. The params dataclass must have `last_entry_minutes_before_close` and `flatten_minutes_before_close` (the judge's noise test nudges them). Reuse `strategies/session_clock.py`, `indicators.py`, `tests/helpers.py`. Tests in `tests/test_strategy_<name>.py`: no look-ahead (changing a later bar never changes an earlier decision), the entry and exit rules on hand-made bars, flat before the close. Run only your own tests while working (`uv run pytest tests/test_strategy_<name>.py`), the full `uv run pytest` once at the end; a failure in another family's half-written file is not yours.
6. **Judge.** `uv run -m judge.scorecard <strategy> --symbol QQQ --set name=value --trials N`. The printed PASS and FAIL lines decide, not your opinion. `--trials` is the honest count of settings tried for this strategy, never lower than the ledger holds. The strategy must be in `STRATEGIES` in `judge/scorecard.py`; that file is locked. If it is not there, write `research/requests/<id>_strategies_entry.md` (exact import lines and the dict entry, with the tunable settings and ranges, and why those ranges), set the journal Status to `blocked: needs STRATEGIES entry`, commit the code, tests, spec, card and request, and stop. The user adds the entry by hand and reruns the idea with `scripts/research_batch.sh --only <id>`.
7. **Card.** Fill in the card: for every judge run the exact command and the **whole printed scorecard pasted verbatim in a code fence** (starting at the line `SCORECARD  ...` and ending at `OVERALL: ...`). `research/leaderboard.py` reads those blocks; a summary table alone is invisible to it. Then the verdict (PASS only if all 8 rules pass on the pre-registered settings), what the numbers mean in plain English, and the next step. Add the lessons that a later researcher needs.
8. **Journal.** In `research/journal.md` update the row's Status and Result cells (keep the table's 10 columns, one line per row), add a line to the Log, and fix "Last updated". Re-read the file right before editing and replace only your own row: other researchers may be editing other rows in the same checkout.
9. **Commit** straight to `main`, by explicit path (see below).

## Working in a shared checkout

Several researchers can run at once in the same working tree. So:

- Commit only your own files: `git add <your paths>` then `git commit -m "<message>" -- <your paths>`. Never `git add -A`, `git add .` or `git commit -a`. Retry after a second if git says `index.lock` exists.
- `research/trials.csv` and `research/audit_log.jsonl` are written by the judge on every run and are locked against you, so you cannot `git add` them. The batch script commits them between ideas. If the Stop hook complains about a dirty tree and the only dirty files are those two or someone else's, you are done: stop.
- Do not run `research/leaderboard.py`; the batch script runs it once at the end.
- The judge already uses every CPU core. Do not start extra parallel jobs of your own.

## Lessons from earlier ideas (read these before designing anything)

- **Check costs first (H1).** The reversion strategies lose after costs before regimes matter. Before building a filter or a variant, look at the base strategy's break-even cost multiple (rule 6) and its zero-cost result. A filter cannot rescue a gross edge smaller than the costs.
- **The engine shows a strategy only the last 200 bars** (about 2.5 sessions). A rule needing longer history (a 14-day average) needs an engine change first, and the engine is locked: say so in the card instead.
- **The judge's walk-forward tunes every setting in `STRATEGIES` (H2).** It cannot hold a setting fixed, so rule 3 does not distinguish variants that differ in a tunable setting. Read rule 3 together with rule 4 (fixed settings on the same months), and say so on the card.
- **The research cache is about 18 months, roughly 370 sessions** (2024-10-21 to 2026-04-21; the later six months are the user's holdout and you cannot read them). Older cards say "two years": they were judged before the holdout was split off. Every variant is a trial; count it.
- **Long-only.** The engine does not short. If the paper's edge needs shorting, you are testing half of it; say so.
- **Published Sharpes are upper bounds.** Replications of the same authors' papers report weaker results.
