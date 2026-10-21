"""Place a few SPY orders on the IB Gateway PAPER account. Paper trading only, 1 share each."""

import math

from ib_async import IB, LimitOrder, MarketOrder, Order, Stock

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 4  # Must be unique per connection to the Gateway.
QTY = 1
WAIT = 20  # seconds to wait for each order to reach a final state

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")


def connect():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
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


def main():
    ib = connect()
    hook_events(ib)
    try:
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

        print("\n5) Trailing stop sell (1% trail) for the share from step 2")
        trail = Order(action="SELL", orderType="TRAIL", totalQuantity=QTY, trailingPercent=1.0, tif="DAY")
        t = ib.placeOrder(spy, trail)
        ib.sleep(3)
        print(f"  status={t.orderStatus.status}")

        print("\nOpen orders:")
        ib.reqAllOpenOrders()
        ib.sleep(2)
        for tr in ib.openTrades():
            o = tr.order
            print(f"  id={o.orderId} parentId={o.parentId} {o.action} {o.totalQuantity} {tr.contract.symbol} "
                  f"{o.orderType} lmt={o.lmtPrice} aux={o.auxPrice} trail%={o.trailingPercent} -> {tr.orderStatus.status}")
        print("\nPositions:")
        for p in ib.positions():
            print(f"  {p.account} {p.contract.symbol} {p.position} @ avg cost {p.avgCost}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
