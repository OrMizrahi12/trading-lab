"""uv run -m live.probe reject [--symbol SPY]

Makes IB itself refuse an order, to show what our code does with a real refusal. Paper trading only.

It sends ONE buy of 1 share through the normal path (`Broker` with the risk limits, `Executor`), priced at 90% of the
last bar's close plus half a cent. SPY and QQQ trade in whole cents, so IB answers with error 110 ("The price does not
conform to the minimum price variation for this contract") and the order never goes live. If IB were to accept it
anyway, the order sits far under the market and is cancelled at once, and the probe says so.

Expected: the refusal is logged with IB's code and reason, `orders sent` is 1, no second attempt follows, and
nothing is held or resting. Uses IB client ID 6: do not run it while a runner is up.
"""

import argparse
import sys

from ib_async import Stock

from .broker import Broker, PaperGuardError, connect_paper
from .config import STATE_DIR
from .execution import Executor, OrderRejected
from .feed import BarFeed
from .reconcile import ib_shares, open_orders
from .risk import Risk, RiskRefused
from .state import lock, run_dir

NAME = "probe_reject"


def probe_reject(ib, *, symbol: str, price: float, risk: Risk | None, out=print, executor: Executor | None = None) -> int:
    """0: IB refused it and we logged and stopped. 1: anything else (accepted, or something broke)."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    broker = Broker(ib, f"live:{NAME}:{symbol}", risk=risk)
    ex = executor or Executor(ib, broker, contract, timeout_s=10)
    broker = ex.broker
    bad = round(price * 0.9, 2) + 0.005  # far under the market, and not a multiple of one cent
    ex._limit = lambda action, ref, widen=0: bad
    out(f"sending   : BUY 1 {symbol} LMT @ {bad} (last close {price:.2f}); IB should refuse the price increment")
    code = 1
    try:
        ex._send("BUY", 1, price)
        out("UNEXPECTED: IB did not refuse it (the order was cancelled again if it was working)")
    except OrderRejected as e:
        out(f"REFUSED BY IB: {e}")
        code = 0
    except RiskRefused as e:
        out(f"risk gate : {e.reason}")
    ib.sleep(2)  # long enough for a retry loop, if there were one, to show itself
    out(f"orders sent by this run: {broker.sent}")
    left = open_orders(ib, symbol)
    out(f"end state : {symbol} held {ib_shares(ib, symbol)}, open orders {len(left)}")
    ok = code == 0 and broker.sent == 1 and not left and ib_shares(ib, symbol) == 0
    out("RESULT    : " + ("OK (refused once, logged with IB's code and reason, no retry)" if ok else "NOT AS EXPECTED"))
    return 0 if ok else 1


def main() -> None:
    from .proof import setup_logging

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["reject"])
    ap.add_argument("--symbol", default="SPY")
    args = ap.parse_args()
    directory = run_dir(NAME, args.symbol, STATE_DIR)
    setup_logging(directory)
    ib = None
    try:
        ib = connect_paper()
        contract = Stock(args.symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        closed = BarFeed(ib, contract).poll()
        if closed.empty:
            raise SystemExit("no bars from IB: cannot take a reference price")
        with lock(directory):
            code = probe_reject(ib, symbol=args.symbol, price=float(closed["close"].iloc[-1]), risk=Risk.from_file())
        print(f"log       : {directory / 'events.log'}")
    except PaperGuardError as e:
        print(f"refused   : {e}")
        code = 2
    finally:
        if ib is not None:
            ib.disconnect()
    sys.exit(code)


if __name__ == "__main__":
    main()
