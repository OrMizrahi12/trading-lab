"""Read-only queries on the trade journal for the dashboard. Paper trading only.

The journal (`live/journal.py`) is written by the engine. This module opens the same SQLite file read-only (`mode=ro`
plus `query_only`), so it can neither create nor change anything, and it never selects the `account` column, so the
account ID cannot reach a page. It does not import `live.journal` on purpose: that class creates tables when opened.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "live" / "journal.sqlite3"

# One row per entry ref, symbol and day. `bought`/`sold` are shares; a position is closed when they are equal.
_BY_ENTRY = """
    SELECT COALESCE(entry_ref, '(not ours)') AS entry_ref, symbol, day,
           MIN(time_et) AS opened, MAX(time_et) AS last_fill,
           SUM(CASE WHEN side = 'BOT' THEN shares ELSE 0 END) AS bought,
           SUM(CASE WHEN side = 'SLD' THEN shares ELSE 0 END) AS sold,
           SUM(CASE WHEN side = 'BOT' THEN shares * price ELSE 0 END) AS bought_value,
           SUM(CASE WHEN side = 'SLD' THEN shares * price ELSE 0 END) AS sold_value,
           SUM(COALESCE(commission, 0)) AS commission,
           SUM(commission IS NULL) AS pending_commission
    FROM fills GROUP BY 1, symbol, day ORDER BY opened"""


def connect(path: Path) -> sqlite3.Connection | None:
    """A read-only connection, or None if the journal file does not exist yet (it is never created here)."""
    path = Path(path)
    if not path.exists():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only = ON")
    db.row_factory = sqlite3.Row
    return db


def _round(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(x, n)


def _entries(db: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """(closed trades, open positions) from the fills."""
    trades, positions = [], []
    for r in db.execute(_BY_ENTRY):
        bought, sold = r["bought"], r["sold"]
        if bought == sold:
            gross = r["sold_value"] - r["bought_value"]
            trades.append({
                "entry_ref": r["entry_ref"], "symbol": r["symbol"], "day": r["day"], "opened": r["opened"],
                "closed": r["last_fill"], "shares": bought,
                "buy_price": _round(r["bought_value"] / bought) if bought else None,
                "sell_price": _round(r["sold_value"] / sold) if sold else None,
                "gross_pnl": _round(gross), "commission": _round(r["commission"]),
                "net_pnl": _round(gross - r["commission"]),
                "commission_pending": bool(r["pending_commission"])})
        else:
            qty = bought - sold
            positions.append({
                "entry_ref": r["entry_ref"], "symbol": r["symbol"], "day": r["day"], "opened": r["opened"],
                "shares": qty, "side": "long" if qty > 0 else "short",
                "avg_entry_price": _round(r["bought_value"] / bought) if bought and qty > 0
                else _round(r["sold_value"] / sold) if sold else None})
    return trades, positions


def stats(trades: list[dict]) -> dict:
    """Simple numbers over closed trades. Net P&L is after commission; a trade still waiting for IB's commission
    report is counted with the commission known so far and flagged in `pending_commission`."""
    wins = [t for t in trades if t["net_pnl"] > 0]
    return {
        "trades": len(trades),
        "win_rate": _round(len(wins) / len(trades) * 100, 1) if trades else None,
        "net_pnl": _round(sum(t["net_pnl"] for t in trades), 2),
        "commissions": _round(sum(t["commission"] for t in trades), 2),
        "pending_commission": sum(t["commission_pending"] for t in trades)}


def equity(db: sqlite3.Connection) -> list[dict]:
    """Net liquidation (account value) per New York day: the day's first and latest reading."""
    return [{"day": r["day"], "open": r["open"], "close": r["close"]} for r in db.execute(
        """SELECT day, SUM(open_net_liquidation) AS open, SUM(net_liquidation) AS close
           FROM snapshots GROUP BY day ORDER BY day""")]


def load(path: Path = DEFAULT_DB) -> dict:
    """Everything the page shows, as plain JSON-able data."""
    db = connect(path)
    if db is None:
        return {"available": False, "equity": [], "positions": [], "trades": [], "stats": stats([])}
    try:
        trades, positions = _entries(db)
        return {"available": True, "equity": equity(db), "positions": positions,
                "trades": list(reversed(trades)), "stats": stats(trades)}
    finally:
        db.close()
