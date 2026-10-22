"""A fake ib_async.IB for the live engine tests (grows as the engine does)."""

from types import SimpleNamespace

from ib_async import Event


class FakeTrade:
    """What placeOrder returns: the order plus its status, fills and open/done state."""

    def __init__(self, contract, order):
        self.contract = contract
        self.order = order
        self.orderStatus = SimpleNamespace(status="Submitted", filled=0, remaining=order.totalQuantity, avgFillPrice=0.0)

    def isDone(self):
        return self.orderStatus.status in ("Filled", "Cancelled", "Inactive")


class FakeIB:
    def __init__(self, accounts=("DU1234567",)):
        self.accounts = list(accounts)
        self.connected = False
        self.connect_args = None
        self.market_data_type = None
        self.placed = []  # (contract, order) for every placeOrder
        self.cancelled = []
        self.trades = []  # every FakeTrade ever placed (or put there by a test)
        self.holdings = {}  # symbol -> shares
        self.net_liquidation = 100_000.0
        self.next_order_id = 1
        self.errorEvent = Event()

    def connect(self, host, port, clientId=0, **kwargs):
        self.connected = True
        self.connect_args = (host, port, clientId, kwargs)

    def disconnect(self):
        self.connected = False

    def isConnected(self):
        return self.connected

    def managedAccounts(self):
        return self.accounts

    def reqMarketDataType(self, kind):
        self.market_data_type = kind

    def sleep(self, seconds):
        return True

    def positions(self):
        return [SimpleNamespace(account=self.accounts[0], contract=SimpleNamespace(symbol=s), position=n, avgCost=0.0)
                for s, n in self.holdings.items() if n]

    def reqAllOpenOrders(self):
        return self.openTrades()

    def openTrades(self):
        return [t for t in self.trades if not t.isDone()]

    def accountSummary(self, account=""):
        return [SimpleNamespace(tag="NetLiquidation", value=str(self.net_liquidation), currency="USD")]

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def placeOrder(self, contract, order):
        if not getattr(order, "orderId", 0):
            order.orderId = self.next_order_id
        self.next_order_id = max(self.next_order_id, order.orderId) + 1
        self.placed.append((contract, order))
        trade = FakeTrade(contract, order)
        self.trades.append(trade)
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order)
        for t in self.trades:
            if t.order is order or t.order.orderId == order.orderId:
                t.orderStatus.status = "Cancelled"
