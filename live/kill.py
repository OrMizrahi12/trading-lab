"""uv run -m live.kill [--clear]

The kill switch. Paper trading only. In this order:
  1. writes the STOP file (`data/live/STOP`): from now on every buy is refused and a runner stops at its next loop;
  2. cancels every open order of ours (orderRef starting with `live:`); orders of anyone else are left alone;
  3. sells the position in each symbol that is ours (a symbol with an order of ours at IB or a runner state file);
  4. checks that none of our orders are left and those symbols are flat, and says so.
Safe to run twice. The runner refuses to start while STOP exists; `uv run -m live.kill --clear` removes it.

It connects with IB client ID 6, like the runner. A running runner holds that ID, so it is asked to leave first
(it sees STOP within one poll, about a minute) and the connection is retried until it has.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
from ib_async import Stock

from .broker import Broker, PaperGuardError, check_paper, connect_paper
from .config import STATE_DIR
from .execution import CANCEL_WAIT_S, ExecutionHalt, Executor
from .reconcile import ib_shares
from .risk import OUR_PREFIX, stop_path
from .state import PositionState

log = logging.getLogger("live")
TAG = "live:kill"


def main_precheck(root: Path = STATE_DIR) -> str | None:
    """A message if the STOP file exists (callers refuse to connect or trade), else None."""
    path = stop_path(root)
    if path.exists():
        return f"STOP file {path} exists (kill switch): refusing to start. Remove it with `uv run -m live.kill --clear`."
    return None


def clear(root: Path = STATE_DIR) -> bool:
    path = stop_path(root)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def _write_stop(root: Path, now: pd.Timestamp) -> None:
    path = stop_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"kill switch {now.isoformat()}\n")
    os.replace(tmp, path)


def _state_symbols(root: Path) -> set[str]:
    out = set()
    for path in Path(root).glob("*/state.json"):  # not state_dryrun.json: a dry run holds nothing
        try:
            out.add(json.loads(path.read_text())["symbol"])
        except (ValueError, KeyError, OSError):
            continue
    return out


def _our_open(ib):
    ib.reqAllOpenOrders()
    return [t for t in ib.openTrades() if t.order.orderRef.startswith(OUR_PREFIX)]


def kill(ib, *, root: Path = STATE_DIR, price, out=print, clock=lambda: pd.Timestamp.now(tz="UTC")) -> int:
    """Run the kill switch. `price(symbol)` gives a reference price for the exit. Returns 0 if flat and clean, else 1."""
    out(f"account   : {', '.join(check_paper(ib))} (paper)")
    _write_stop(root, clock())
    out(f"STOP      : written to {stop_path(root)}; buys are refused and the runner will not (re)start")
    problems = []
    broker = Broker(ib, TAG)  # no risk limits here: the kill switch must never be blocked

    orders = _our_open(ib)
    symbols = {t.contract.symbol for t in orders} | _state_symbols(root)
    if orders:
        for t in orders:
            out(f"cancelling: #{t.order.orderId} {t.contract.symbol} {t.order.action} {t.order.orderType} "
                f"{t.order.totalQuantity} [{t.order.orderRef}]")
            broker.cancel(t.order)
        for _ in range(CANCEL_WAIT_S * 2):
            if all(t.isDone() for t in orders):
                break
            ib.sleep(0.5)
        stuck = [t for t in orders if not t.isDone()]
        if stuck:
            problems.append(f"cancel not confirmed for {[t.order.orderId for t in stuck]}")
            out(f"PROBLEM   : {problems[-1]}")
    else:
        out("orders    : nothing to cancel")

    for symbol in sorted(symbols):
        held = ib_shares(ib, symbol)
        if held <= 0:
            out(f"position  : {symbol} {held}, nothing to sell")
            continue
        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        pos = PositionState(shares=held, entry_time=clock().isoformat(), entry_price=0.0, stop=0.0, target=None)
        out(f"selling   : {held} {symbol}")
        try:
            Executor(ib, broker, contract, clock=clock).exit(pos, float(price(symbol)), restore_protection=False)
        except ExecutionHalt as e:
            problems.append(f"{symbol}: {e}")
            out(f"PROBLEM   : {problems[-1]}")
        except Exception as e:  # e.g. no price: say so and go on to the next symbol
            problems.append(f"{symbol}: could not sell: {e}")
            out(f"PROBLEM   : {problems[-1]}")

    ib.sleep(1)
    left_orders = _our_open(ib)
    not_flat = {s: ib_shares(ib, s) for s in sorted(symbols) if ib_shares(ib, s) != 0}
    out("check     : " + (f"{len(left_orders)} order(s) of ours still open" if left_orders else "no open orders of ours"))
    out("check     : " + (f"NOT FLAT {not_flat}" if not_flat else f"flat in {sorted(symbols) or 'nothing (no symbol of ours)'}"))
    ok = not (problems or left_orders or not_flat)
    out("RESULT    : " + ("OK. STOP exists; remove it with `uv run -m live.kill --clear` to trade again."
                         if ok else "FAILED: check IB by hand. STOP exists."))
    return 0 if ok else 1


def _connect(out):
    """Client ID 6 may still be held by a runner that has not yet seen STOP: retry for about two minutes."""
    for attempt in range(13):
        try:
            return connect_paper()
        except PaperGuardError:
            raise
        except Exception as e:
            out(f"connect   : not yet ({type(e).__name__}); a runner may still hold client ID 6, retrying")
            time.sleep(10)
    raise SystemExit("could not connect to the paper Gateway; STOP is written, the runner will not start")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clear", action="store_true", help="remove the STOP file (no connection, nothing is traded)")
    args = ap.parse_args()
    if args.clear:
        print("STOP file removed." if clear() else "No STOP file.")
        return
    logging.getLogger().handlers[:] = []
    _write_stop(STATE_DIR, pd.Timestamp.now(tz="UTC"))  # first, even if the connection takes a while
    handler = logging.FileHandler(STATE_DIR / "kill.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    ib = None
    try:
        ib = _connect(print)
        from .feed import BarFeed

        def price(symbol: str) -> float:
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            try:
                closed = BarFeed(ib, contract).poll()
                if not closed.empty:
                    return float(closed["close"].iloc[-1])
            except Exception as e:
                log.warning("kill: no bars for %s (%s), using the average cost", symbol, e)
            costs = [p.avgCost for p in ib.positions() if p.contract.symbol == symbol and p.avgCost]
            if not costs:
                raise RuntimeError("no price")
            return float(costs[0])

        code = kill(ib, price=price)
    except PaperGuardError as e:
        print(f"refused   : {e}")
        code = 2
    finally:
        if ib is not None:
            ib.disconnect()
    sys.exit(code)


if __name__ == "__main__":
    main()
