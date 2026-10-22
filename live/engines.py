"""The engines file: which (strategy, symbol) pairs one process runs. Paper trading only.

    engines:
      - {strategy: opening_range_breakout, symbol: QQQ, settings: {range_minutes: 15}}
      - {strategy: donchian_breakout, symbol: SPY}

One engine per symbol: IB holds ONE position per symbol and reconcile compares the state file with it, so two
engines on the same symbol would each see the other's shares as unexplained. Limits per symbol come from
risk_limits.yaml; a symbol without limits is refused at load, not discovered at the first buy.
Times in settings must be quoted ("11:30"): unquoted, YAML reads them as a number.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .registry import STRATEGIES, build, parse_value

DEFAULT_ENGINES = Path(__file__).parent / "engines.yaml"


@dataclass(frozen=True)
class Pair:
    strategy: str
    symbol: str
    settings: dict = field(default_factory=dict, compare=False)

    @property
    def label(self) -> str:
        return f"{self.symbol} {self.strategy}"


def load_pairs(path: Path = DEFAULT_ENGINES, risk=None) -> list[Pair]:
    """The pairs in the file, validated. Raises ValueError with the file name and the entry at fault."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"{path}: {e}") from e
    entries = raw.get("engines") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: needs a non-empty `engines:` list")
    pairs, seen = [], {}
    for i, entry in enumerate(entries, 1):
        where = f"{path}: engine {i}"
        if not isinstance(entry, dict) or not {"strategy", "symbol"} <= set(entry) or set(entry) - {"strategy", "symbol", "settings"}:
            raise ValueError(f"{where}: needs strategy and symbol, allows settings (got {entry!r})")
        strategy, symbol = entry["strategy"], str(entry["symbol"]).upper()
        if strategy not in STRATEGIES:
            raise ValueError(f"{where}: unknown strategy {strategy!r}; choose from {sorted(STRATEGIES)}")
        if not symbol.isalpha():
            raise ValueError(f"{where}: {symbol!r} does not look like a stock symbol")
        settings = entry.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError(f"{where}: settings must be a mapping of name: value")
        settings = {k: parse_value(v) if isinstance(v, str) else v for k, v in settings.items()}
        try:
            build(strategy, settings)  # the same construction the runner does: a bad setting name fails here
        except (TypeError, ValueError) as e:
            raise ValueError(f"{where}: {strategy} {settings}: {e}") from e
        if symbol in seen:
            raise ValueError(f"{where}: {symbol} is already used by engine {seen[symbol]}; "
                             "one engine per symbol (IB keeps one position per symbol)")
        seen[symbol] = i
        if risk is not None and symbol not in risk.limits.symbols:
            raise ValueError(f"{where}: no limits for {symbol} in risk_limits.yaml (a symbol without limits cannot be bought)")
        pairs.append(Pair(strategy, symbol, settings))
    return pairs
