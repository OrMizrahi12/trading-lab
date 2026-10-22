"""The trade journal: one SQLite file with the engine's signals, IB's orders and fills (with commission) and a daily
account snapshot. Paper trading only.

Written by the engine (`Runner` calls `signal` for every decision and `sync` after every poll) and by the one-off
import in `live/journal_import.py`, which uses this same class. Nothing in the strategies, the backtester or the
research code touches it. The file is `data/live/journal.sqlite3` (gitignored). It is a record, not a control: a failed
write is logged and never stops trading.

Tables: `signals` (what a strategy decided and what was done), `orders` (one row per IB order, keyed by IB's permId; quantity, filled and average price come from the fills when IB omits them),
`fills` (one row per execution, keyed by IB's execId; `commission` stays NULL until IB's report arrives), `snapshots`
(one row per account per New York day: the first and the latest reading). View `entry_pnl`: profit and loss per entry ref.

`sync` is idempotent: it reads what IB reports (orders, executions with their commission reports) and upserts, so the
same fill is never counted twice and a late commission fills in the NULL. Times are UTC (`time_utc`), plus New York
time (`time_et`) and the New York session date (`day`) for fills.

Fills are tied to the entry that opened them (`entry_ref`, e.g. `live:opening_range_breakout:QQQ:2026-10-22:0945`):
the buy and the resting stop/target carry that ref at IB; a sell the engine sends itself carries only the plain tag
`live:<strategy>:<symbol>`, so it is tied to the latest buy of that tag the same day (one position per symbol at a time).
Sqlite3 examples:

    SELECT time_et, side, symbol, shares, price, commission FROM fills WHERE day = date('now') ORDER BY time_utc;
    SELECT * FROM entry_pnl ORDER BY day, entry_ref;
"""

import logging
import math
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger("live")

NY = "America/New_York"
UNSET = 1e300  # ib_async marks an unset float with sys.float_info.max

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    engine TEXT NOT NULL,        -- live:<strategy>:<symbol>
    bar_utc TEXT NOT NULL,       -- start of the bar that gave the signal
    kind TEXT NOT NULL,          -- entry | exit
    reason TEXT,
    stop REAL,
    target REAL,
    shares INTEGER,              -- entries only: shares sized (0 = skipped)
    action TEXT,                 -- what was done: bought, sold, skipped: ..., not filled, dry run
    entry_ref TEXT,              -- entries only: the orderRef its orders carry
    seen_utc TEXT,
    age_s INTEGER,               -- seconds between the bar's close and the engine seeing it
    dry_run INTEGER NOT NULL DEFAULT 0,
    UNIQUE (engine, bar_utc, kind, dry_run)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    perm_id INTEGER UNIQUE,      -- IB's permanent id; NULL until IB assigns one
    client_id INTEGER,
    order_id INTEGER,
    day TEXT NOT NULL,           -- New York session date the order was first seen
    account TEXT,
    symbol TEXT,
    action TEXT,                 -- BUY | SELL
    order_type TEXT,             -- LMT | STP | ...
    tif TEXT,
    quantity REAL,
    limit_price REAL,
    stop_price REAL,
    order_ref TEXT,
    entry_ref TEXT,              -- order_ref when it is an entry's ref, else NULL
    oca_group TEXT,
    parent_id INTEGER,
    status TEXT,
    filled REAL,
    avg_fill_price REAL,
    first_seen_utc TEXT,
    updated_utc TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    exec_id TEXT PRIMARY KEY,
    perm_id INTEGER,
    order_id INTEGER,
    client_id INTEGER,
    account TEXT,
    symbol TEXT,
    side TEXT,                   -- BOT | SLD
    shares REAL,
    price REAL,
    time_utc TEXT,
    time_et TEXT,
    day TEXT,                    -- New York session date
    exchange TEXT,
    order_ref TEXT,
    entry_ref TEXT,              -- the entry this fill belongs to (see the module docstring), NULL if not ours
    commission REAL,             -- NULL until IB's commission report arrives
    commission_currency TEXT,
    realized_pnl REAL,           -- IB's own figure on a closing fill, NULL if not reported
    first_seen_utc TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    day TEXT NOT NULL,           -- New York date
    account TEXT NOT NULL,
    open_net_liquidation REAL,   -- the day's first reading
    net_liquidation REAL,        -- the latest reading
    total_cash REAL,
    gross_position_value REAL,
    unrealized_pnl REAL,
    realized_pnl REAL,
    taken_utc TEXT,
    PRIMARY KEY (day, account)
);
CREATE INDEX IF NOT EXISTS fills_by_day ON fills (day, symbol, time_utc);
DROP VIEW IF EXISTS entry_pnl;
CREATE VIEW entry_pnl AS
    SELECT entry_ref, symbol, day, bought, sold,
           CASE WHEN bought = sold THEN ROUND(gross, 4) END AS gross_pnl,
           ROUND(commission, 4) AS commission,
           CASE WHEN bought = sold THEN ROUND(gross - commission, 4) END AS net_pnl,
           fills_without_commission,
           CASE WHEN bought = sold THEN 'closed' ELSE 'open' END AS status
    FROM (SELECT COALESCE(entry_ref, '(not ours)') AS entry_ref, symbol, day,
                 SUM(CASE WHEN side = 'BOT' THEN shares ELSE 0 END) AS bought,
                 SUM(CASE WHEN side = 'SLD' THEN shares ELSE 0 END) AS sold,
                 SUM(CASE WHEN side = 'SLD' THEN shares * price ELSE -shares * price END) AS gross,
                 SUM(COALESCE(commission, 0)) AS commission,
                 SUM(commission IS NULL) AS fills_without_commission
          FROM fills GROUP BY 1, symbol, day);
PRAGMA user_version = 1;
"""


def _num(x) -> float | None:
    """A float, or None for a missing, NaN or IB-'unset' value."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or abs(v) >= UNSET else v


def _utc(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _stamp(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")


def _day(ts: pd.Timestamp) -> str:
    return ts.tz_convert(NY).strftime("%Y-%m-%d")


def is_entry_ref(ref: str | None) -> bool:
    """`live:<strategy>:<symbol>:<date>:<HHMM>`, the ref of an entry bracket (live/refs.py)."""
    return bool(ref) and ref.startswith("live:") and ref.count(":") == 4


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class Journal:
    def __init__(self, path: Path, clock=_utc_now):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)  # a second process (the import) may hold the lock briefly
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- signals -------------------------------------------------------------------------

    def signal(self, engine: str, record: dict, *, entry_ref: str | None = None, dry_run: bool = False) -> None:
        """One decision of a strategy, as the runner's `signals.jsonl` record. Recording it again updates the row."""
        target = record.get("target")
        with self.db:
            self.db.execute(
                """INSERT INTO signals (engine, bar_utc, kind, reason, stop, target, shares, action, entry_ref,
                                        seen_utc, age_s, dry_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (engine, bar_utc, kind, dry_run) DO UPDATE SET
                       reason = excluded.reason, stop = excluded.stop, target = excluded.target,
                       shares = excluded.shares, action = excluded.action, entry_ref = excluded.entry_ref,
                       seen_utc = excluded.seen_utc, age_s = excluded.age_s""",
                (engine, _stamp(_utc(record["bar"])), record["kind"], record.get("reason"), _num(record.get("stop")),
                 _num(target), record.get("shares"), record.get("action"), entry_ref,
                 _stamp(_utc(record["seen_at"])) if record.get("seen_at") else None, record.get("age_s"),
                 int(dry_run)))

    # -- orders and fills from IB --------------------------------------------------------

    def sync(self, ib, *, owns=None, full: bool = False, completed: bool = False, now=None,
             commission_wait_s: float = 0.0) -> dict:
        """Upsert what IB reports. `owns(orderRef)` limits it to one engine's orders (None = everything).

        `full` asks IB for all of today's executions (`reqExecutions`); otherwise the executions this connection has
        already seen (`ib.fills()`, which also carries commission reports as they arrive). `completed` adds the
        orders IB finished earlier today, which a new connection does not list. IB sends a commission report a moment
        after its execution, so `commission_wait_s` waits up to that long for the missing ones (the engine does not
        wait: the next poll fills them in).
        """
        now = _utc(now) if now is not None else self.clock()
        trades = list(ib.trades())
        if completed:
            trades += list(ib.reqCompletedOrders(False))
        fills = list(ib.fills())
        if full:  # ib_async hands commission reports to the Fill objects in `ib.fills()`, not to the copies reqExecutions returns
            seen = {f.execution.execId: f for f in ib.fills()}
            fills = [seen.get(f.execution.execId, f) for f in ib.reqExecutions()]
            seen = {f.execution.execId: f for f in ib.fills()}  # now includes the day's earlier executions
            fills = [seen.get(f.execution.execId, f) for f in fills]
        waited = 0.0
        while waited < commission_wait_s and any(not f.commissionReport.execId for f in fills):
            ib.sleep(0.5)  # the reports update the Fill objects in place
            waited += 0.5
        before = self._count()
        with self.db:
            for t in trades:
                if owns is None or owns(t.order.orderRef or ""):
                    self._order(t, now)
            for f in fills:
                if owns is None or owns(f.execution.orderRef or ""):
                    self._fill(f, now)
            self._attribute()
            self._complete_orders()
        after = self._count()
        return {"orders_seen": len(trades), "fills_seen": len(fills),
                "new_orders": after[0] - before[0], "new_fills": after[1] - before[1]}

    def _count(self) -> tuple[int, int]:
        return (self.db.execute("SELECT count(*) FROM orders").fetchone()[0],
                self.db.execute("SELECT count(*) FROM fills").fetchone()[0])

    def _order(self, trade, now: pd.Timestamp) -> None:
        o, st = trade.order, trade.orderStatus
        perm = o.permId or None
        oid = o.orderId or None
        if perm is None and oid is None:
            return  # nothing to tell it apart by
        day = _day(now)
        row = None
        if perm:
            row = self.db.execute("SELECT id FROM orders WHERE perm_id = ?", (perm,)).fetchone()
        if row is None and oid:  # first seen before IB gave it a permId
            row = self.db.execute("SELECT id FROM orders WHERE client_id IS ? AND order_id = ? AND day = ? AND perm_id IS NULL",
                                  (o.clientId or None, oid, day)).fetchone()
        ref = o.orderRef or None
        values = dict(perm_id=perm, client_id=o.clientId or None, order_id=oid, account=o.account or None,
                      symbol=trade.contract.symbol, action=o.action, order_type=o.orderType, tif=o.tif or None,
                      quantity=_num(o.totalQuantity), limit_price=_num(o.lmtPrice) if o.orderType in ("LMT", "STP LMT") else None,
                      stop_price=_num(o.auxPrice) if o.orderType in ("STP", "STP LMT") else None,
                      order_ref=ref, entry_ref=ref if is_entry_ref(ref) else None, oca_group=o.ocaGroup or None,
                      parent_id=o.parentId or None, status=st.status, filled=_num(st.filled),
                      avg_fill_price=_num(st.avgFillPrice) or None, updated_utc=_stamp(now))
        if row is None:
            values.update(day=day, first_seen_utc=_stamp(now))
            cols = ", ".join(values)
            self.db.execute(f"INSERT INTO orders ({cols}) VALUES ({', '.join('?' * len(values))})", list(values.values()))
        else:
            if perm is None:
                values.pop("perm_id")  # never blank a permId we already have
            sets = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(f"UPDATE orders SET {sets} WHERE id = ?", [*values.values(), row[0]])

    def _fill(self, fill, now: pd.Timestamp) -> None:
        ex = fill.execution
        if not ex.execId:
            return
        when = _utc(ex.time) if ex.time is not None and _utc(ex.time).year > 1970 else now
        rep = getattr(fill, "commissionReport", None)
        reported = rep is not None and bool(getattr(rep, "execId", ""))  # an empty report is "not yet", not zero
        ref = ex.orderRef or None
        self.db.execute(
            """INSERT INTO fills (exec_id, perm_id, order_id, client_id, account, symbol, side, shares, price, time_utc,
                                  time_et, day, exchange, order_ref, entry_ref, commission, commission_currency,
                                  realized_pnl, first_seen_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (exec_id) DO UPDATE SET
                   perm_id = COALESCE(excluded.perm_id, perm_id),
                   commission = COALESCE(excluded.commission, commission),
                   commission_currency = COALESCE(excluded.commission_currency, commission_currency),
                   realized_pnl = COALESCE(excluded.realized_pnl, realized_pnl)""",
            (ex.execId, ex.permId or None, ex.orderId or None, ex.clientId or None, ex.acctNumber or None,
             fill.contract.symbol, ex.side, _num(ex.shares), _num(ex.price), _stamp(when),
             when.tz_convert(NY).strftime("%Y-%m-%d %H:%M:%S"), _day(when), ex.exchange or None, ref,
             ref if is_entry_ref(ref) else None,
             _num(rep.commission) if reported else None, (rep.currency or None) if reported else None,
             _num(rep.realizedPNL) if reported else None, _stamp(now)))

    def _attribute(self) -> None:
        """Tie a sell of ours that is not an entry's own order to the entry it closed: the latest buy the same day of
        the same tag (the engine's own exit, `live:<strategy>:<symbol>`), else of any engine (the kill switch, `live:kill`)."""
        latest_buy = """SELECT b.order_ref FROM fills b
                          WHERE b.side = 'BOT' AND b.symbol = fills.symbol AND b.day = fills.day
                            AND b.entry_ref IS NOT NULL AND b.time_utc <= fills.time_utc {same_tag}
                          ORDER BY b.time_utc DESC LIMIT 1"""
        self.db.execute(
            f"""UPDATE fills SET entry_ref = COALESCE(
                   ({latest_buy.format(same_tag="AND substr(b.order_ref, 1, length(fills.order_ref) + 1) = fills.order_ref || ':'")}),
                   ({latest_buy.format(same_tag="")}))
               WHERE entry_ref IS NULL AND side = 'SLD' AND order_ref LIKE 'live:%'""")

    def _complete_orders(self) -> None:
        """IB reports a finished order without its quantity or average price (both come back 0): take them from its fills.
        Filled and average price come from the fills whenever an order has any; the quantity only for a fully filled order
        (a cancelled order's original size is not in the fills)."""
        by_order = "(SELECT {agg} FROM fills WHERE fills.perm_id = orders.perm_id)"
        self.db.execute(
            f"""UPDATE orders SET
                    filled = {by_order.format(agg="SUM(shares)")},
                    avg_fill_price = {by_order.format(agg="SUM(shares * price) / SUM(shares)")},
                    quantity = CASE WHEN status = 'Filled' AND COALESCE(quantity, 0) = 0
                                    THEN {by_order.format(agg="SUM(shares)")} ELSE quantity END
                WHERE perm_id IS NOT NULL AND EXISTS (SELECT 1 FROM fills WHERE fills.perm_id = orders.perm_id)
                  AND (COALESCE(avg_fill_price, 0) = 0 OR COALESCE(filled, 0) = 0 OR COALESCE(quantity, 0) = 0)""")

    # -- daily snapshot ------------------------------------------------------------------

    def snapshot(self, ib, now=None) -> bool:
        """Today's account reading (the first of the day is kept as `open_net_liquidation`). False if IB gave none."""
        now = _utc(now) if now is not None else self.clock()
        account = ib.managedAccounts()[0]
        rows = {r.tag: _num(r.value) for r in ib.accountSummary(account)
                if r.account == account and r.currency in ("USD", "BASE")}
        net = rows.get("NetLiquidation")
        if net is None:
            return False
        with self.db:
            self.db.execute(
                """INSERT INTO snapshots (day, account, open_net_liquidation, net_liquidation, total_cash,
                                          gross_position_value, unrealized_pnl, realized_pnl, taken_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (day, account) DO UPDATE SET
                       net_liquidation = excluded.net_liquidation, total_cash = excluded.total_cash,
                       gross_position_value = excluded.gross_position_value, unrealized_pnl = excluded.unrealized_pnl,
                       realized_pnl = excluded.realized_pnl, taken_utc = excluded.taken_utc""",
                (_day(now), account, net, net, rows.get("TotalCashValue"), rows.get("GrossPositionValue"),
                 rows.get("UnrealizedPnL"), rows.get("RealizedPnL"), _stamp(now)))
        return True
