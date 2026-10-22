"""Strategy name and `--set name=value` settings to a strategy object. Paper trading only."""

import ast
import re
from datetime import time

from strategies.bollinger_rsi_reversion import BollingerRsiReversion, BollingerRsiReversionParams
from strategies.donchian_breakout import DonchianBreakout, DonchianBreakoutParams
from strategies.ema_trend import EmaTrend, EmaTrendParams
from strategies.opening_range_breakout import OpeningRangeBreakout, OpeningRangeBreakoutParams
from strategies.spy_vwap_reclaim import SpyVwapReclaim, SpyVwapReclaimParams
from strategies.vwap_stretch_reversion import VwapStretchReversion, VwapStretchReversionParams
from strategies.vwap_trend import VwapTrend, VwapTrendParams

STRATEGIES = {
    "opening_range_breakout": (OpeningRangeBreakout, OpeningRangeBreakoutParams),
    "donchian_breakout": (DonchianBreakout, DonchianBreakoutParams),
    "ema_trend": (EmaTrend, EmaTrendParams),
    "bollinger_rsi_reversion": (BollingerRsiReversion, BollingerRsiReversionParams),
    "vwap_stretch_reversion": (VwapStretchReversion, VwapStretchReversionParams),
    "vwap_trend": (VwapTrend, VwapTrendParams),
    "spy_vwap_reclaim": (SpyVwapReclaim, SpyVwapReclaimParams),
}


def parse_value(raw: str):
    """'15' -> 15, '1.5' -> 1.5, 'None' -> None, '11:30' -> time(11, 30), anything else stays text."""
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour, minute = raw.split(":")
        return time(int(hour), int(minute))
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


def parse_settings(pairs: list[str]) -> dict:
    settings = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"--set expects name=value, got {pair!r}")
        settings[name] = parse_value(raw)
    return settings


def build(name: str, settings: dict):
    """The strategy exactly as the backtester and the judge construct it: Strategy(Params(**settings))."""
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy {name!r}; choose from {sorted(STRATEGIES)}")
    strategy, params = STRATEGIES[name]
    return strategy(params(**settings))
