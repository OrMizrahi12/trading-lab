"""uv run -m live.journal_import [--db PATH]

Copies today's orders and fills (with commission) and the account snapshot from the IB paper Gateway into the trade
journal (`data/live/journal.sqlite3`). Paper trading only. The connection is READ-ONLY (IB refuses orders on it) and
uses its own client ID, 7, so it can run beside the runner (6), the data pipeline (5) and the scanner (4).

IB only lists the current day's executions to an API client, so run it on the day you want in the journal.
Safe to run twice or beside a running engine: rows are keyed by IB's ids and a repeat changes nothing.
"""

import argparse

from .broker import connect_paper
from .config import JOURNAL_CLIENT_ID, JOURNAL_PATH, PAPER_PORTS, PORT
from .journal import Journal

COMMISSION_WAIT_S = 8.0  # IB sends the commission reports a moment after the executions


def import_from_ib(ib, journal: Journal) -> dict:
    """Everything IB reports for today into the journal, whoever placed it (a `ours` filter is not applied)."""
    counts = journal.sync(ib, full=True, completed=True, commission_wait_s=COMMISSION_WAIT_S)
    counts["snapshot"] = journal.snapshot(ib)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(JOURNAL_PATH), help="journal file (default data/live/journal.sqlite3)")
    ap.add_argument("--port", type=int, default=PORT, choices=sorted(PAPER_PORTS), help="paper ports only")
    args = ap.parse_args()
    ib = connect_paper(port=args.port, client_id=JOURNAL_CLIENT_ID, readonly=True)  # refuses live ports and non-DU accounts
    try:
        print(f"account   : {', '.join(ib.managedAccounts())} (paper), client ID {JOURNAL_CLIENT_ID}, read-only")
        with Journal(args.db) as journal:
            c = import_from_ib(ib, journal)
        print(f"orders    : {c['orders_seen']} reported by IB, {c['new_orders']} new in the journal")
        print(f"fills     : {c['fills_seen']} reported by IB, {c['new_fills']} new in the journal")
        print(f"snapshot  : {'saved' if c['snapshot'] else 'IB gave no NetLiquidation'}")
        print(f"journal   : {args.db}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
