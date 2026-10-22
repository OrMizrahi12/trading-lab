"""A fake ib_async.IB for the live engine tests (grows as the engine does)."""

from types import SimpleNamespace

from ib_async import Event


class FakeTrade:
    """What placeOrder returns: the order plus its status, fills and open/done state."""

    def __init__(self, contract, order):
        self.contract = contract
        self.order = order
        self.orderStatus = SimpleNamespace(status="Submitted", filled=0, remaining=order.totalQuantity, avgFillPrice=0.0)
        self.fills = []  # SimpleNamespace(execution=SimpleNamespace(time, shares, price))

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
        self._trades = []  # every FakeTrade ever placed (or put there by a test)
        self.holdings = {}  # symbol -> shares
        self.net_liquidation = 100_000.0
        self.next_order_id = 1
        self.market_price = None  # a marketable limit fills at this price; None = nothing fills
        self.fill_fraction = 1.0  # share of a marketable order that fills (partial fills)
        self.fractions = []  # per-order fill shares for the next marketable orders (consumed first)
        self.slow_cancel = False  # True: a cancel is never confirmed (status stays PendingCancel)
        self.reject_types = set()  # order types IB rejects, e.g. {"STP"}
        self.reject_code, self.reject_text = 201, "Order rejected - reason: the fake IB refuses this order type"
        self.tick = 0.01  # prices must be a multiple of this, as IB demands (error 110 otherwise)
        self.freeze = False  # True: no order ever fills (timeout tests)
        self.now = None  # time stamped on fills
        self.errorEvent = Event()
        self._forgotten = set()  # trades a restarted session does not know (see restart)

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

    def trades(self):
        return [t for t in self._trades if id(t) not in self._forgotten]

    def openTrades(self):
        return [t for t in self._trades if not t.isDone()]

    def accountSummary(self, account=""):
        return [SimpleNamespace(account=self.accounts[0], tag="NetLiquidation", value=str(self.net_liquidation),
                                currency="USD")]

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def placeOrder(self, contract, order):
        """Like IB: the same orderId again is a modify; transmit=False holds an order until a transmit=True one
        of its bracket arrives; a child (parentId set) sleeps until its parent has filled."""
        known = getattr(order, "orderId", 0) and next((t for t in self._trades if t.order.orderId == order.orderId), None)
        if known:
            known.order = order
            known.orderStatus.remaining = order.totalQuantity - known.orderStatus.filled
            self.placed.append((contract, order))
            return known
        if not getattr(order, "orderId", 0):
            order.orderId = self.next_order_id
        self.next_order_id = max(self.next_order_id, order.orderId) + 1
        self.placed.append((contract, order))
        trade = FakeTrade(contract, order)
        self._trades.append(trade)
        if getattr(order, "orderType", "") in self.reject_types:
            return self._reject(trade, self.reject_code, self.reject_text)
        for price in (getattr(order, "lmtPrice", 0), getattr(order, "auxPrice", 0)):
            if 0 < price < 1e9 and abs(round(price / self.tick) * self.tick - price) > 1e-9:  # 1e9+ is ib_async's "unset"
                return self._reject(trade, 110, "The price does not conform to the minimum price variation for this contract.")
        if not getattr(order, "transmit", True):
            trade.orderStatus.status = "PendingSubmit"
            return trade
        parent_id = getattr(order, "parentId", 0) or order.orderId
        for t in self._trades:  # a transmit releases its whole bracket
            if t.orderStatus.status == "PendingSubmit" and parent_id in (t.order.orderId, getattr(t.order, "parentId", 0)):
                t.orderStatus.status = "PreSubmitted" if getattr(t.order, "parentId", 0) else "Submitted"
        if getattr(order, "parentId", 0):
            trade.orderStatus.status = "PreSubmitted"
        parent = next((t for t in self._trades if t.order.orderId == parent_id), trade)
        if parent.orderStatus.status == "Submitted" and parent.orderStatus.filled == 0:
            self._try_fill(parent)
        elif not getattr(order, "parentId", 0):
            self._try_fill(trade)
        return trade

    def _reject(self, trade, code, text):
        # Seen on the real paper Gateway: ib_async reports error 110 (bad price increment) as Cancelled, not Inactive.
        trade.orderStatus.status = "Cancelled" if code == 110 else "Inactive"
        self.errorEvent.emit(trade.order.orderId, code, text, trade.contract)
        return trade

    def _try_fill(self, trade):
        order, px = trade.order, self.market_price
        if self.freeze or px is None or getattr(order, "orderType", "") != "LMT":
            return
        crosses = order.lmtPrice >= px if order.action == "BUY" else order.lmtPrice <= px
        if crosses and not getattr(order, "ocaGroup", ""):  # a resting target is filled by trigger()
            fraction = self.fractions.pop(0) if self.fractions else self.fill_fraction
            self._fill(trade, max(1, int(order.totalQuantity * fraction)), px)

    def _fill(self, trade, shares, price):
        order, st = trade.order, trade.orderStatus
        shares = min(shares, st.remaining)
        total = st.filled * st.avgFillPrice + shares * price
        st.filled += shares
        st.remaining -= shares
        st.avgFillPrice = total / st.filled
        st.status = "Filled" if st.remaining == 0 else "Submitted"
        sign = 1 if order.action == "BUY" else -1
        symbol = trade.contract.symbol
        self.holdings[symbol] = self.holdings.get(symbol, 0) + sign * shares
        trade.fills.append(SimpleNamespace(execution=SimpleNamespace(
            time=self.now, shares=shares, price=price, orderRef=getattr(order, "orderRef", ""), orderId=order.orderId,
            side="BOT" if order.action == "BUY" else "SLD")))
        for child in self._trades:  # a bracket's children wake up when their parent has a fill
            if getattr(child.order, "parentId", 0) == order.orderId and child.orderStatus.status == "PreSubmitted":
                child.orderStatus.status = "Submitted"
        if st.status == "Filled" and getattr(order, "ocaGroup", ""):  # one-cancels-other
            for other in self._trades:
                if other is not trade and getattr(other.order, "ocaGroup", "") == order.ocaGroup and not other.isDone():
                    other.orderStatus.status = "Cancelled"

    def trigger(self, trade, price):
        """A resting stop or target gets hit at `price`."""
        self._fill(trade, trade.orderStatus.remaining, price)

    def cancelOrder(self, order):
        self.cancelled.append(order)
        for t in self._trades:
            if t.order is order or t.order.orderId == order.orderId:
                t.orderStatus.status = "PendingCancel" if self.slow_cancel else "Cancelled"
                if not self.slow_cancel and not t.orderStatus.filled:  # IB drops the children of an unfilled parent
                    for child in self._trades:
                        if getattr(child.order, "parentId", 0) == t.order.orderId and not child.isDone():
                            child.orderStatus.status = "Cancelled"

    def reqExecutions(self, execFilter=None):
        """Today's executions, as ib_async's Fill objects: .execution has orderRef, side, shares, price, orderId."""
        return [f for t in self._trades for f in t.fills]

    def restart(self):
        """The runner died and a new process connected: IB still has the open orders, the fills and the position,
        but the new session's `trades()` no longer lists orders that were already done."""
        self._forgotten = {id(t) for t in self._trades if t.isDone()}
