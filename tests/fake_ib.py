"""A fake ib_async.IB for the live engine tests (grows as the engine does)."""

from types import SimpleNamespace

from ib_async import Event


class FakeIB:
    def __init__(self, accounts=("DU1234567",)):
        self.accounts = list(accounts)
        self.connected = False
        self.connect_args = None
        self.market_data_type = None
        self.placed = []  # (contract, order) for every placeOrder
        self.cancelled = []
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

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return SimpleNamespace(contract=contract, order=order)

    def cancelOrder(self, order):
        self.cancelled.append(order)
