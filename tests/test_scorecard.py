"""Offline tests for the pure parts of judge/scorecard.py (no backtests are run)."""

import numpy as np

from judge.scorecard import STRATEGIES, break_even, deflated_sharpe, load_standards, noise_versions

RULE_KEYS = {"min_trades", "min_sharpe", "min_walk_forward_sharpe", "max_drawdown_p5",
             "min_break_even_multiple", "min_deflated_sharpe", "min_noise_profitable_share"}


def test_standards_has_every_rule():
    std = load_standards()
    assert set(std["rules"]) == RULE_KEYS
    assert std["rules"]["min_trades"] == 200
    assert std["rules"]["max_drawdown_p5"] < 0  # a drawdown limit is a negative fraction


def test_break_even_interpolates_between_swept_multiples():
    assert break_even({0: 100.0, 1: 50.0, 2: -50.0}) == 1.5
    assert break_even({0: 100.0, 1: 50.0, 2: 10.0}) is None  # still profitable at the top
    assert break_even({0: -5.0, 1: -20.0}) == 0.0  # loses even for free


def test_deflated_sharpe_falls_as_trials_rise():
    rng = np.random.default_rng(1)
    daily = rng.normal(0.0006, 0.005, 500)  # a decent strategy
    trial_sharpes = rng.normal(1.0, 0.5, 40)
    few, _ = deflated_sharpe(daily, trial_sharpes, 1)
    many, bar = deflated_sharpe(daily, trial_sharpes, 1000)
    assert many < few and bar > 0


def test_deflated_sharpe_of_pure_noise_is_low():
    daily = np.random.default_rng(2).normal(0, 0.005, 500)
    dsr, _ = deflated_sharpe(daily, np.random.default_rng(3).normal(0, 0.5, 40), 100)
    assert dsr < 0.5


def test_noise_versions_stay_near_the_settings_and_on_the_step():
    cfg = {"versions": 50, "relative": 0.25, "last_entry_minutes": 20, "seed": 11}
    out = noise_versions("opening_range_breakout", {"range_minutes": 20, "target_r": 2.0}, cfg)
    assert len(out) == 50
    assert all(10 <= v["range_minutes"] <= 25 and v["range_minutes"] % 5 == 0 for v in out)
    assert all(1.5 <= v["target_r"] <= 2.5 for v in out)
    assert len({v["last_entry_minutes_before_close"] for v in out}) > 1
    for v in out:  # every nudged version must still build
        STRATEGIES["opening_range_breakout"].params(**v)


def test_noise_keeps_a_missing_target_missing():
    cfg = {"versions": 5, "relative": 0.25, "last_entry_minutes": 20, "seed": 1}
    out = noise_versions("opening_range_breakout", {"range_minutes": 15}, cfg)
    assert all("target_r" not in v for v in out)
