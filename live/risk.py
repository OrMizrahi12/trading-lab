"""Risk limits outside the strategy, and the STOP file. Paper trading only.

`Broker.place` asks `Risk.check` before every order leaves the program. Only buys (orders that add to a position)
can be refused; sells and cancels reduce risk and always go out, so a stop, an exit and the kill switch can never be
blocked by a limit. A refused order raises `RiskRefused`, is logged with its reason (log, console and
`risk_refusals.jsonl` next to the state files) and nothing is sent.

Limits come from `risk_limits.yaml`; a missing or malformed file is an error, never a default. A symbol without
limits cannot be bought. The STOP file (`data/live/STOP`, written by `uv run -m live.kill`) refuses every buy.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from .config import STATE_DIR
from .console import say

log = logging.getLogger("live")

DEFAULT_LIMITS = Path(__file__).parent / "risk_limits.yaml"
OUR_PREFIX = "live:"  # every order we send carries an orderRef that starts with this
# The desk project (~/trading-desk) shares this paper account and also starts its refs with "live:". Its orders are
# never ours: the kill switch must not cancel them, and its buys must not count against our trade limit.
DESK_PREFIXES = ("live:desk:", "live:mcp:", "live:open:")


def is_ours(ref: str | None) -> bool:
    ref = ref or ""
    return ref.startswith(OUR_PREFIX) and not ref.startswith(DESK_PREFIXES)


class RiskRefused(RuntimeError):
    """An order was refused by a risk limit. Nothing was sent."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SymbolLimits:
    max_shares: int
    max_position_value: float


@dataclass(frozen=True)
class RiskLimits:
    symbols: dict[str, SymbolLimits]
    max_trades_per_day: int
    daily_loss_limit: float


def stop_path(root: Path = STATE_DIR) -> Path:
    return Path(root) / "STOP"


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number, got {value!r}")
    return value


def load_limits(text: str) -> RiskLimits:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("risk limits: the file must be a mapping")
    unknown = set(raw) - {"symbols", "max_trades_per_day", "daily_loss_limit"}
    missing = {"symbols", "max_trades_per_day", "daily_loss_limit"} - set(raw)
    if unknown or missing:
        raise ValueError(f"risk limits: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    if not isinstance(raw["symbols"], dict) or not raw["symbols"]:
        raise ValueError("risk limits: `symbols` needs at least one symbol")
    symbols = {}
    for name, body in raw["symbols"].items():
        if not isinstance(body, dict) or set(body) != {"max_shares", "max_position_value"}:
            raise ValueError(f"risk limits: {name} needs exactly max_shares and max_position_value")
        shares = _positive(body["max_shares"], f"{name}.max_shares")
        if shares != int(shares):
            raise ValueError(f"{name}.max_shares must be a whole number")
        symbols[str(name)] = SymbolLimits(int(shares), float(_positive(body["max_position_value"], f"{name}.max_position_value")))
    trades = _positive(raw["max_trades_per_day"], "max_trades_per_day")
    return RiskLimits(symbols, int(trades), float(_positive(raw["daily_loss_limit"], "daily_loss_limit")))


def net_liquidation(ib) -> float:
    """The first managed (paper) account's total value, cash plus positions, in dollars."""
    account = ib.managedAccounts()[0]
    for row in ib.accountSummary(account):
        if row.tag == "NetLiquidation" and row.currency in ("USD", "BASE") and row.account == account:
            return float(row.value)
    raise RuntimeError("IB did not report NetLiquidation")


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class Risk:
    def __init__(self, limits: RiskLimits, root: Path = STATE_DIR, clock=_utc_now):
        self.limits, self.root, self.clock = limits, Path(root), clock

    @classmethod
    def from_file(cls, path: Path = DEFAULT_LIMITS, root: Path = STATE_DIR) -> "Risk":
        return cls(load_limits(Path(path).read_text()), root)

    # -- what IB holds ---------------------------------------------------------------------

    @staticmethod
    def _held(ib, symbol: str) -> int:
        return int(sum(p.position for p in ib.positions() if p.contract.symbol == symbol))

    @staticmethod
    def _resting_buys(ib, symbol: str) -> int:
        ib.reqAllOpenOrders()
        return int(sum(t.orderStatus.remaining for t in ib.openTrades()
                       if t.contract.symbol == symbol and t.order.action == "BUY"))

    def _entries_today(self, ib) -> int:
        """Distinct entry refs of ours that bought today or are still buying (IB's executions and open orders)."""
        refs = {f.execution.orderRef for f in ib.reqExecutions()
                if f.execution.side == "BOT" and is_ours(f.execution.orderRef)}
        refs |= {t.order.orderRef for t in ib.openTrades()
                 if t.order.action == "BUY" and is_ours(t.order.orderRef)}
        return len(refs)

    def _day_start_equity(self, ib) -> float:
        """Net liquidation at the first check of the New York day, kept in risk_day.json."""
        today = f"{self.clock().tz_convert('America/New_York'):%Y-%m-%d}"
        path = self.root / "risk_day.json"
        try:
            saved = json.loads(path.read_text())
            if saved["date"] == today:
                return float(saved["start_equity"])
        except (FileNotFoundError, ValueError, KeyError):
            pass
        equity = net_liquidation(ib)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": today, "start_equity": equity}))
        os.replace(tmp, path)
        return equity

    # -- sizing ----------------------------------------------------------------------------

    def cap_shares(self, ib, symbol: str, shares: int, price: float) -> int:
        """`shares` cut down to what still fits under the symbol's limits (a symbol without limits is left to `check`)."""
        lim = self.limits.symbols.get(symbol)
        if lim is None:
            return shares
        used = self._held(ib, symbol) + self._resting_buys(ib, symbol)
        room = min(lim.max_shares, int(lim.max_position_value // price)) - used
        capped = max(0, min(shares, room))
        if capped < shares:
            log.warning("risk: %s size clamped from %d to %d shares (max_shares %d, max_position_value %.0f, "
                        "already %d held or resting)", symbol, shares, capped, lim.max_shares, lim.max_position_value, used)
        return capped

    # -- the gate --------------------------------------------------------------------------

    def check(self, ib, contract, order) -> None:
        """Raise RiskRefused if this order breaks a limit. Sells always pass."""
        if order.action != "BUY":
            return
        symbol, qty = contract.symbol, int(order.totalQuantity)
        # Only a real limit order has a price to value; a market order carries ib_async's "unset" (a huge float).
        price = float(order.lmtPrice) if getattr(order, "orderType", "LMT") == "LMT" and order.lmtPrice else 0.0
        reason = self._reason(ib, symbol, qty, price)
        if reason:
            self._refuse(order, symbol, qty, price, reason)

    def _reason(self, ib, symbol: str, qty: int, price: float) -> str | None:
        if stop_path(self.root).exists():
            return f"STOP file exists ({stop_path(self.root)}): the kill switch was used, no new buys"
        lim = self.limits.symbols.get(symbol)
        if lim is None:
            return f"no limits configured for {symbol} in risk_limits.yaml (default deny)"
        if price <= 0:
            return "a buy needs a limit price so its value can be checked"
        lost = self._day_start_equity(ib) - net_liquidation(ib)
        if lost >= self.limits.daily_loss_limit:
            return f"daily_loss_limit: down {lost:.2f} today, limit {self.limits.daily_loss_limit:.2f}"
        if self._entries_today(ib) >= self.limits.max_trades_per_day:
            return f"max_trades_per_day: {self.limits.max_trades_per_day} entries already today"
        total = self._held(ib, symbol) + self._resting_buys(ib, symbol) + qty
        if total > lim.max_shares:
            return f"max_shares: {total} {symbol} would be held or resting, limit {lim.max_shares}"
        if total * price > lim.max_position_value:
            return (f"max_position_value: {total} {symbol} at {price:.2f} is {total * price:.0f}, "
                    f"limit {lim.max_position_value:.0f}")
        return None

    def _refuse(self, order, symbol: str, qty: int, price: float, reason: str) -> None:
        log.warning("RISK REFUSED: BUY %d %s @ %s: %s", qty, symbol, price or "-", reason)
        say(f"RISK REFUSED: BUY {qty} {symbol} @ {price or '-'}: {reason}")
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "risk_refusals.jsonl").open("a") as f:
            f.write(json.dumps({"time": self.clock().isoformat(), "symbol": symbol, "action": order.action,
                                "quantity": qty, "price": price, "orderRef": getattr(order, "orderRef", ""),
                                "reason": reason}) + "\n")
        raise RiskRefused(reason)
