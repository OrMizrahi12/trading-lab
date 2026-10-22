"""Place a few SPY orders on the IB Gateway PAPER account. Paper trading only, 1 share each."""

import math
import sys
import time

import pandas as pd
from ib_async import IB, Forex, LimitOrder, MarketOrder, Stock, StopOrder

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 4  # Must be unique per connection to the Gateway.
QTY = 1
WAIT = 20  # seconds to wait for each order to reach a final state

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")


TWS_PORT = 7497  # TWS paper port (the Gateway paper port is 4002)
TWS_CLIENT_ID = 5


def connect(port=PORT, client_id=CLIENT_ID):
    if port not in PAPER_PORTS:
        raise RuntimeError(f"Refusing to connect: port {port} is not a paper port")
    ib = IB()
    ib.connect(HOST, port, clientId=client_id)
    accounts = ib.managedAccounts()
    if not accounts or not all(a.startswith("DU") for a in accounts):
        ib.disconnect()
        raise RuntimeError(f"Refusing to trade: accounts {accounts} are not all DU paper accounts")
    print(f"Connected to paper account(s): {accounts}")
    return ib


def hook_events(ib):
    ib.orderStatusEvent += lambda t: print(
        f"  [status] order {t.order.orderId} {t.order.action} {t.order.orderType}: {t.orderStatus.status} "
        f"filled={t.orderStatus.filled} remaining={t.orderStatus.remaining} avg={t.orderStatus.avgFillPrice}"
    )
    ib.execDetailsEvent += lambda t, f: print(
        f"  [fill] order {f.execution.orderId} {f.execution.side} {f.execution.shares} @ {f.execution.price}"
    )
    ib.commissionReportEvent += lambda t, f, r: print(f"  [commission] order {f.execution.orderId}: {r.commission} {r.currency}")


def wait_done(ib, trade, timeout=WAIT):
    waited = 0.0
    while not trade.isDone() and waited < timeout:
        ib.sleep(0.5)
        waited += 0.5
    return trade.isDone()


def market_price(ib, contract):
    for data_type in (1, 3):  # live, then delayed
        ib.reqMarketDataType(data_type)
        t = ib.reqMktData(contract, "", True)
        ib.sleep(4)
        ib.cancelMktData(contract)
        for p in (t.last, t.marketPrice(), t.close):
            if p is not None and not math.isnan(p) and p > 0:
                print(f"SPY reference price {p} (market data type {data_type})")
                return p
    raise RuntimeError("No SPY price available")


def show_state(ib):
    print("\nOpen orders:")
    ib.reqAllOpenOrders()
    ib.sleep(2)
    for tr in ib.openTrades():
        o = tr.order
        print(f"  id={o.orderId} parentId={o.parentId} {o.action} {o.totalQuantity} {tr.contract.symbol} "
              f"{o.orderType} lmt={o.lmtPrice} aux={o.auxPrice} oca={o.ocaGroup or '-'} -> {tr.orderStatus.status}")
    if not ib.openTrades():
        print("  (none)")
    print("\nPositions:")
    for p in ib.positions():
        print(f"  {p.account} {p.contract.symbol} {p.position} @ avg cost {p.avgCost}")
    if not ib.positions():
        print("  (none)")


def oca_exits(ib, contract, price):
    """Two sell exits for the same share in one OCA group (one-cancels-all): a fill of one cancels the other."""
    take_profit = LimitOrder("SELL", QTY, round(price * 1.01, 2), tif="DAY")
    stop_loss = StopOrder("SELL", QTY, round(price * 0.99, 2), tif="DAY")
    group = f"oca-spy-{int(time.time())}"
    ib.oneCancelsAll([take_profit, stop_loss], group, 1)  # type 1 = cancel the rest on fill, with block
    trades = [ib.placeOrder(contract, o) for o in (take_profit, stop_loss)]
    ib.sleep(3)
    for t in trades:
        o = t.order
        print(f"  {o.action} {o.orderType} id={o.orderId} lmt={o.lmtPrice} stop={o.auxPrice} "
              f"ocaGroup={o.ocaGroup} ocaType={o.ocaType} -> {t.orderStatus.status}")


def cancel_trailing_stops(ib):
    ib.reqAllOpenOrders()
    ib.sleep(2)
    for tr in ib.openTrades():
        if tr.order.orderType == "TRAIL":
            print(f"  cancelling trailing stop id={tr.order.orderId}")
            ib.cancelOrder(tr.order)
            wait_done(ib, tr)
            print(f"  status={tr.orderStatus.status}")


def forex_demo(ib):
    """Buy EURUSD with cashQty (spend a fixed 20,000 USD) on IDEALPRO and print whatever IB answers."""
    eur = Forex("EURUSD")  # IDEALPRO is IB's forex venue
    ib.qualifyContracts(eur)
    order = MarketOrder("BUY", 0, tif="DAY")  # quantity 0: the size comes from cashQty instead
    order.cashQty = 20000
    t = ib.placeOrder(eur, order)
    print(f"  done={wait_done(ib, t)} status={t.orderStatus.status}")
    for e in t.log:
        print(f"  IB log: {e.status} {e.message} (error code {e.errorCode})")


def flatten_all(ib):
    """Cancel every open order, then close every position at market. Paper account only (guarded in connect())."""
    print("  cancelling all open orders")
    ib.reqGlobalCancel()
    waited = 0.0
    while waited < WAIT and any(not t.isDone() for t in ib.openTrades()):
        ib.sleep(0.5)
        waited += 0.5
    closing = []
    for p in ib.positions():
        if not p.position:
            continue
        contract = p.contract
        qty = abs(p.position)
        if contract.secType == "STK":
            contract.exchange = "SMART"  # a position reports its fill venue (ARCA); direct routing is rejected
        elif contract.secType == "CASH":
            qty = math.floor(qty)  # IDEALPRO rejects fractional units; a sub-1-unit remainder cannot be closed
        ib.qualifyContracts(contract)
        action = "SELL" if p.position > 0 else "BUY"
        print(f"  closing {p.contract.symbol} {p.position}: {action} {qty} at market")
        closing.append(ib.placeOrder(contract, MarketOrder(action, qty, tif="DAY")))
    for t in closing:
        print(f"  {t.contract.symbol}: done={wait_done(ib, t)} status={t.orderStatus.status}")
    ib.sleep(2)


def followup(ib):
    print("\nA) Cancel the trailing stop")
    cancel_trailing_stops(ib)
    spy = Stock("SPY", "SMART", "USD")
    ib.qualifyContracts(spy)
    price = market_price(ib, spy)
    print("\nB) OCA group exits")
    oca_exits(ib, spy, price)
    print("\nC) EURUSD cashQty 20000 USD on IDEALPRO")
    forex_demo(ib)
    print("\nD) flatten_all()")
    flatten_all(ib)
    show_state(ib)


def bar_price(ib, contract):
    """Reference price from the last 5-minute bar's close, for when quotes do not reach the API."""
    bars = ib.reqHistoricalData(contract, "", "1 D", "5 mins", "TRADES", useRTH=True, formatDate=2)
    if not bars:
        raise RuntimeError("No historical SPY bars available")
    last = bars[-1]
    age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(last.date).tz_convert("UTC")).total_seconds() / 60
    print(f"SPY reference price {last.close} = close of the last 5-minute bar (start {last.date}, "
          f"{age:.0f} min ago), NOT a live quote")
    return last.close


def tws_demo(ib):
    """TWS paper walk-through: what-if, a resting limit buy that we cancel, then a marketable limit round trip."""
    spy = Stock("SPY", "SMART", "USD")
    ib.qualifyContracts(spy)
    price = bar_price(ib, spy)

    print("\n1) What-if market buy (places nothing)")
    s = ib.whatIfOrder(spy, MarketOrder("BUY", QTY, tif="DAY"))
    if isinstance(s, list) or s is None:
        raise RuntimeError(f"what-if returned no order state: {s!r}")
    print(f"  initial margin change={s.initMarginChange} maintenance margin change={s.maintMarginChange} "
          f"equity with loan after={s.equityWithLoanAfter}")
    print(f"  commission={s.commission} (min {s.minCommission}, max {s.maxCommission}) {s.commissionCurrency}")

    print("\n2) Limit buy 0.50 below market, then cancel")
    limit = round(price - 0.50, 2)
    t = ib.placeOrder(spy, LimitOrder("BUY", QTY, limit, tif="DAY"))
    ib.sleep(3)
    print(f"  limit {limit} status={t.orderStatus.status}; cancelling")
    ib.cancelOrder(t.order)
    print(f"  done={wait_done(ib, t)} status={t.orderStatus.status}")

    print("\n3) Marketable limit buy 0.50 above market (fills at once if the market is open)")
    buy = ib.placeOrder(spy, LimitOrder("BUY", QTY, round(price + 0.50, 2), tif="DAY"))
    print(f"  done={wait_done(ib, buy)} status={buy.orderStatus.status} filled={buy.orderStatus.filled}")
    if buy.orderStatus.filled < QTY:
        print("  not filled: cancelling it and NOT selling, since nothing was bought")
        ib.cancelOrder(buy.order)
        print(f"  done={wait_done(ib, buy)} status={buy.orderStatus.status}")
        return

    print("\n4) Matching marketable limit sell 0.50 below market to close")
    sell = ib.placeOrder(spy, LimitOrder("SELL", buy.orderStatus.filled, round(price - 0.50, 2), tif="DAY"))
    print(f"  done={wait_done(ib, sell)} status={sell.orderStatus.status} filled={sell.orderStatus.filled}")
    show_state(ib)


def main(stage="orders"):
    if stage == "tws":
        ib = connect(TWS_PORT, TWS_CLIENT_ID)
        hook_events(ib)
        ib.errorEvent += lambda reqId, code, msg, contract: print(f"  [ib message] {code}: {msg}")
        try:
            return tws_demo(ib)
        finally:
            ib.disconnect()
    ib = connect()
    hook_events(ib)
    try:
        if stage == "followup":
            return followup(ib)
        if stage == "flatten":
            flatten_all(ib)
            return show_state(ib)
        spy = Stock("SPY", "SMART", "USD")
        ib.qualifyContracts(spy)
        price = market_price(ib, spy)

        print("\n1) What-if market buy (places nothing)")
        s = ib.whatIfOrder(spy, MarketOrder("BUY", QTY, tif="DAY"))
        if isinstance(s, list) or s is None:
            raise RuntimeError(f"what-if returned no order state: {s!r}")
        print(f"  initial margin change={s.initMarginChange} maintenance margin change={s.maintMarginChange} "
              f"equity with loan after={s.equityWithLoanAfter}")
        print(f"  commission={s.commission} (min {s.minCommission}, max {s.maxCommission}) {s.commissionCurrency}")

        print("\n2) Market buy")
        t = ib.placeOrder(spy, MarketOrder("BUY", QTY, tif="DAY"))
        print(f"  done={wait_done(ib, t)} status={t.orderStatus.status}")

        print("\n3) Limit buy 5% below market, then cancel")
        limit = round(price * 0.95, 2)
        t = ib.placeOrder(spy, LimitOrder("BUY", QTY, limit, tif="DAY"))
        ib.sleep(3)
        print(f"  limit {limit} status={t.orderStatus.status}; cancelling")
        ib.cancelOrder(t.order)
        print(f"  done={wait_done(ib, t)} status={t.orderStatus.status}")

        print("\n4) Bracket: limit buy near market, take profit +1%, stop loss -1%")
        entry = round(price - 0.10, 2)  # a touch under the market so it rests instead of filling at once
        parent, take_profit, stop_loss = ib.bracketOrder(
            "BUY", QTY, entry, takeProfitPrice=round(entry * 1.01, 2), stopLossPrice=round(entry * 0.99, 2)
        )
        # parent and take-profit are held back (transmit False); the stop loss is sent last with
        # transmit True, which releases all three together. Children link to the parent via parentId.
        for o in (parent, take_profit, stop_loss):
            o.tif = "DAY"  # explicit, so IB does not answer with a "TIF set from preset" notice
            print(f"  {o.action} {o.orderType} id={o.orderId} parentId={o.parentId} transmit={o.transmit}")
        trades = [ib.placeOrder(spy, o) for o in (parent, take_profit, stop_loss)]
        ib.sleep(3)
        print("  statuses:", [t.orderStatus.status for t in trades])

        print("\n5) OCA group: take-profit and stop-loss exits for the held share")
        oca_exits(ib, spy, price)

        show_state(ib)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "orders")
