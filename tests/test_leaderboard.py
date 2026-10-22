"""Offline tests for research/leaderboard.py (no backtests are run)."""

import json

import numpy as np
import pandas as pd

from research.leaderboard import (Run, drop_correlated, drop_duplicates, load_audit, parse_card, rank, render)

CARD = """
```
SCORECARD  opening_range_breakout on QQQ 5min  {'range_minutes': 15, 'target_r': 1.0}
data 2024-10-21 to 2026-10-21, usual costs, 1% risk per trade; took 82 s

#  rule                              result     needs   verdict
1  Trades                               354    >= 200   PASS
2  Sharpe after costs                  1.77    >= 1.0   PASS
3  Walk-forward Sharpe                 0.41    >= 0.5   FAIL
4  Fixed-settings walk-forward         1.50    >= 0.5   PASS
5  Monte Carlo drawdown, P5           -8.9%   >= -20%   PASS
6  Break-even cost multiple        above 5x   >= 2.0x   PASS
7  Deflated Sharpe                    87.6%    >= 95%   FAIL
8  Parameter noise                     100%    >= 80%   PASS

  7. after 326 trials, bar to beat 0.94

OVERALL: FAIL (6 of 8 rules passed)
```
"""


def make_run(strategy="s", symbol="QQQ", settings=None, source="audit", passed=4, dsr="50.0%", sharpe="1.0"):
    rules = [(f"r{i}", "1", i < passed) for i in range(8)]
    rules[1] = ("Sharpe after costs", sharpe, rules[1][2])
    rules[6] = ("Deflated Sharpe", dsr, rules[6][2])
    return Run(strategy, symbol, settings or {}, source, "w", rules)


def test_parse_card_reads_a_scorecard_block():
    [run], bad = parse_card(CARD, "x.md")
    assert bad == 0
    assert (run.strategy, run.symbol, run.settings) == ("opening_range_breakout", "QQQ", {"range_minutes": 15, "target_r": 1.0})
    assert run.n_passed == 6 and run.trials == 326
    assert run.value("Sharpe after costs") == 1.77
    assert run.value("Break-even cost multiple") == 5.0  # "above 5x"
    assert run.value("Monte Carlo drawdown, P5") == -8.9


def test_parse_card_default_settings_and_unreadable_block():
    [run], _ = parse_card(CARD.replace("{'range_minutes': 15, 'target_r': 1.0}", "(default settings)"), "x.md")
    assert run.settings == {}
    runs, bad = parse_card(CARD.replace("{'range_minutes': 15, 'target_r': 1.0}", "{oops"), "x.md")
    assert runs == [] and bad == 1
    runs, bad = parse_card(CARD.replace("8  Parameter noise                     100%    >= 80%   PASS\n", ""), "x.md")
    assert runs == [] and bad == 1  # a block without all 8 rules is not trusted


def test_load_audit_skips_crashes(tmp_path):
    ok = {"time": "t1", "strategy": "s", "symbol": "SPY", "settings": {}, "dirty": True, "standards_sha256": "abc",
          "trials": {"used": 100}, "result": {"passed": False, "rules": [{"name": "Trades", "value": "5", "needs": ">= 200", "passed": False}]}}
    crash = {**ok, "time": "t2", "result": {"error": "KeyboardInterrupt()"}}
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps(ok) + "\n" + json.dumps(crash) + "\n\n")
    runs, crashed = load_audit(path)
    assert crashed == 1 and len(runs) == 1
    assert runs[0].dirty and runs[0].trials == 100 and runs[0].source == "audit"


def test_duplicates_audit_beats_card_and_later_beats_earlier():
    key = lambda r: (r.strategy, r.symbol, json.dumps(r.settings, sort_keys=True))
    card, early, late = make_run(source="card"), make_run(source="audit"), make_run(source="audit")
    early.where, late.where = "t1", "t2"
    kept, dropped = drop_duplicates([card, early, late], key)
    assert kept == [late]
    assert len(dropped) == 2
    kept, dropped = drop_duplicates([early, card], key)  # a card never replaces an audit run
    assert kept == [early] and dropped == [(card, early)]
    other = make_run(settings={"x": 1})
    assert len(drop_duplicates([early, other], key)[0]) == 2  # different settings are different runs


def test_rank_orders_by_rules_then_deflated_sharpe_then_sharpe():
    a = make_run("a", passed=5, dsr="10.0%")
    b = make_run("b", passed=6, dsr="5.0%")
    c = make_run("c", passed=5, dsr="30.0%")
    d = make_run("d", passed=5, dsr="30.0%", sharpe="2.0")
    assert [r.strategy for r in rank([a, b, c, d])] == ["b", "d", "c", "a"]


def daily(seed, n=100):
    return pd.Series(np.random.default_rng(seed).normal(0, 0.01, n), index=pd.date_range("2025-01-01", periods=n).astype(str))


def test_correlated_run_is_dropped_in_favour_of_the_better_one():
    base = daily(1)
    ranked = [make_run("best"), make_run("copy"), make_run("other")]
    returns = {0: base, 1: base * 0.5 + 0.0001, 2: daily(2)}
    kept, dropped = drop_correlated(ranked, returns, 0.7)
    assert [r.strategy for r in kept] == ["best", "other"]
    [(run, twin, c)] = dropped
    assert run.strategy == "copy" and twin.strategy == "best" and c > 0.99


def test_negatively_correlated_and_missing_returns_stay():
    base = daily(1)
    ranked = [make_run("a"), make_run("inverse"), make_run("unknown")]
    kept, dropped = drop_correlated(ranked, {0: base, 1: -base, 2: None}, 0.7)
    assert len(kept) == 3 and not dropped  # -1 is not "above 0.7"; no returns means no check


def test_only_kept_runs_are_compared():
    base = daily(1)
    ranked = [make_run("a"), make_run("b"), make_run("c")]
    # b copies a (dropped). c copies b only through a shared noise term, but is unrelated to a: stays.
    kept, dropped = drop_correlated(ranked, {0: base, 1: base, 2: daily(3)}, 0.7)
    assert [r.strategy for r in kept] == ["a", "c"] and len(dropped) == 1


def test_short_or_flat_series_are_not_compared():
    short = daily(1, 10)
    flat = pd.Series(0.0, index=daily(1).index)
    kept, dropped = drop_correlated([make_run("a"), make_run("b"), make_run("c")], {0: short, 1: short, 2: flat}, 0.7)
    assert len(kept) == 3 and not dropped


def test_render_lists_board_and_drops():
    best, copy = make_run("best", passed=8), make_run("copy")
    text = render([best], [(make_run("old"), best)], [(copy, best, 0.93)],
                  {"audit": 2, "card": 1, "crashed": 0, "unreadable": 0}, 0.7, None)
    assert "| 1 | best | QQQ |" in text and "0.93" in text and "exact duplicate" in text
    assert "Runs that passed all 8 rules: best QQQ" in text


def test_card_run_that_saw_the_holdout_period_is_flagged():
    from research.leaderboard import flags
    [run], _ = parse_card(CARD.replace("(default", "(default"), "x.md")
    run.span_end = "2026-10-21"
    assert "holdout" in flags(run, None)
    run.span_end = "2026-04-21"  # the current cache ends the day before the holdout starts
    assert "holdout" not in flags(run, None)
