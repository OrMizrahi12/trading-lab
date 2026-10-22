"""uv run -m live.proof [--symbol SPY]

Proof that the live order path does not send the same entry twice, without waiting for a strategy signal.
Paper trading only: 1 share, and the position is flat again when the command ends.

What one run does, through the same code the live runner uses (`Broker`, `Executor.enter`, `Executor.exit`):
  1. connects to the paper Gateway (refuses anything else), requires an open session and a flat symbol;
  2. builds the entry ref `live:proof_entry:SPY:<session date>:<session open bar>`: the same for every run today;
  3. looks for that ref in IB's open orders, today's executions and this session's orders;
  4. not found: sends ONE bracket (buy 1 share with a marketable limit + stop 2% below + target 2% above, all
     tagged with the ref), shows the three orders, then sells the share and cancels the stop and target;
     found: sends nothing and says where IB already has it;
  5. checks that the symbol is flat and prints how many orders this run sent.
Run it twice: the first run sends the bracket (3 orders) and the exit (1 sell), the second sends 0.

The price comes from the last closed 5-minute bar (about 15-20 minutes late, the account has no real-time data),
so the stop and target are loose and the 0.5% marketable limit can miss a fast market; then nothing is bought.
It uses IB client ID 6, like the live runner: do not run both at once.
"""

import argparse
import logging
import sys

import exchange_calendars as xcals
import pandas as pd
from ib_async import Stock

from .broker import Broker, PaperGuardError, check_paper, connect_paper
from .config import FLATTEN_BEFORE_CLOSE_MIN, STATE_DIR
from .execution import DuplicateEntry, ExecutionHalt, Executor, utc_now
from .feed import BarFeed
from .reconcile import ib_shares, open_orders
from .refs import entry_ref, order_label, owns
from .state import PositionState, lock, run_dir

NAME = "proof_entry"
SHARES = 1
STOP_PCT, TARGET_PCT = 0.02, 0.02


def session_open(now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(open, close) of today's NYSE session in UTC, or None when the exchange is shut today."""
    cal = xcals.get_calendar("XNYS")
    day = pd.Timestamp(now.tz_convert("America/New_York").date())
    try:
        return cal.session_open(day), cal.session_close(day)
    except xcals.errors.NotSessionError:
        return None


def describe(trade) -> str:
    o = trade.order
    price = getattr(o, "lmtPrice", 0) or getattr(o, "auxPrice", 0)
    parent = f" parent {o.parentId}" if getattr(o, "parentId", 0) else ""
    return f"{order_label(o)} {o.action} {o.orderType} {o.totalQuantity} @ {price} {o.tif}{parent} [{trade.orderStatus.status}] ref {o.orderRef}"


def prove(ib, *, symbol: str = "SPY", price, clock=utc_now, out=print, executor: Executor | None = None) -> int:
    """Run the proof once. `price()` gives the reference price. Returns the exit code: 0 proven, 1 failed, 2 refused."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    accounts = check_paper(ib)
    now = clock()
    out(f"account   : {', '.join(accounts)} (paper)")
    hours = session_open(now)
    if hours is None or not hours[0] <= now < hours[1] - pd.Timedelta(minutes=FLATTEN_BEFORE_CLOSE_MIN):
        out("refused   : the market is closed (or about to close). Run it during the regular session, 09:30-16:00 ET.")
        return 2
    tag = f"live:{NAME}:{symbol}"
    ref = entry_ref(tag, hours[0])  # the session's opening bar: one ref per day, however often the command runs
    ex = executor or Executor(ib, Broker(ib, tag), contract, clock=clock)
    broker = ex.broker
    out(f"entry ref : {ref}")

    held, foreign = ib_shares(ib, symbol), [t for t in open_orders(ib, symbol) if not owns(tag, t.order.orderRef)]
    if held or foreign or open_orders(ib, symbol):
        out(f"refused   : {symbol} is not flat ({held} held, {len(open_orders(ib, symbol))} open order(s), "
            f"{len(foreign)} of them foreign); fix that first.")
        return 2

    ref_price = float(price())
    stop, target = round(ref_price * (1 - STOP_PCT), 2), round(ref_price * (1 + TARGET_PCT), 2)
    out(f"price     : last closed bar {ref_price:.2f} -> stop {stop:.2f}, target {target:.2f}, {SHARES} share")
    try:
        entered = ex.enter(ref, SHARES, ref_price, stop, target)
    except DuplicateEntry as dup:
        out("the ref is already known, so NOTHING is sent:")
        for line in dup.found:
            out(f"            - {line}")
        return finish(ib, broker, symbol, out, expect_sent=0)
    except ExecutionHalt as e:
        out(f"HALT      : {e}. Check IB by hand: {symbol} may be open.")
        return 1
    if entered is None:
        out("not filled: the marketable limit did not fill in time and was cancelled (nothing bought, nothing resting).")
        return finish(ib, broker, symbol, out, expect_sent=None)

    f = entered.fill
    out(f"bought    : {f.shares} @ {f.price:.2f}")
    for t in ib.trades():
        if t.order.orderRef == ref:
            out(f"            {describe(t)}")
    pos = PositionState(shares=f.shares, entry_time=f.time.isoformat(), entry_price=f.price, stop=stop, target=target,
                        stop_order_id=entered.stop_order_id, target_order_id=entered.target_order_id)
    for _ in range(20):  # IB's position report trails the fill by a moment; the exit sells only what it sees
        if ib_shares(ib, symbol) >= f.shares:
            break
        ib.sleep(0.5)
    try:
        sold = ex.exit(pos, ref_price)
    except ExecutionHalt as e:
        out(f"HALT      : {e}. {symbol} may still be open; its stop/target rest at IB. Check by hand.")
        return 1
    out("sold      : " + (f"{sold.shares} @ {sold.price:.2f}, stop and target cancelled" if sold else "nothing left to sell"))
    return finish(ib, broker, symbol, out, expect_sent=None)


def finish(ib, broker: Broker, symbol: str, out, *, expect_sent: int | None) -> int:
    ib.sleep(1)
    flat = ib_shares(ib, symbol) == 0 and not open_orders(ib, symbol)
    out(f"orders sent by this run: {broker.sent}")
    out(f"end state : {symbol} " + ("flat, no open orders" if flat else "NOT FLAT: check IB by hand"))
    ok = flat and (expect_sent is None or broker.sent == expect_sent)
    out("RESULT    : " + ("OK" if ok else "FAILED") + (" (the ref was not sent twice)" if expect_sent == 0 and ok else ""))
    return 0 if ok else 1


def setup_logging(directory) -> None:
    """Everything the libraries and the live code log (ib_async included) goes to events.log; the console only gets
    the proof's own lines."""
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / "events.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]  # no console handler, whoever added one
    root.setLevel(logging.INFO)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY")
    args = ap.parse_args()
    setup_logging(run_dir(NAME, args.symbol, STATE_DIR))
    ib = None
    try:
        ib = connect_paper()
        contract = Stock(args.symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        feed = BarFeed(ib, contract)

        def price() -> float:
            closed = feed.poll()
            if closed.empty:
                raise SystemExit("no bars from IB: cannot take a reference price")
            return float(closed["close"].iloc[-1])

        with lock(run_dir(NAME, args.symbol, STATE_DIR)):
            code = prove(ib, symbol=args.symbol, price=price)
    except PaperGuardError as e:
        print(f"refused   : {e}")
        code = 2
    finally:
        if ib is not None:
            ib.disconnect()
    sys.exit(code)


if __name__ == "__main__":
    main()
