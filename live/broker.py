"""Paper-only connection and order gate. Paper trading only.

Every order goes through `Broker`, which re-checks the connection and the account
(a DU prefix is IB's mark of a simulated account) before each placeOrder.
"""

import logging

from ib_async import IB

from .config import CLIENT_ID, HOST, PAPER_PORTS, PORT
from .refs import owns
from .risk import Risk

log = logging.getLogger("live")

# IB error codes that arrive on an order id without meaning it was refused: 202 order cancelled, 399 order warning.
NOT_A_REFUSAL = {202, 399}
DELAYED = 3  # IB market data type: delayed data, since the account has no real-time subscription


class PaperGuardError(RuntimeError):
    """The connection or account is not a paper one; nothing may be traded."""


def check_paper(ib, host: str = HOST, port: int = PORT) -> list[str]:
    """Raise PaperGuardError unless host, port and every managed account are paper. Return the accounts."""
    if host not in {"127.0.0.1", "localhost"}:
        raise PaperGuardError(f"Host {host} is not local")
    if port not in PAPER_PORTS:
        raise PaperGuardError(f"Port {port} is not a paper port")
    accounts = ib.managedAccounts()
    if not accounts or not all(a.startswith("DU") for a in accounts):
        raise PaperGuardError(f"Non-paper account(s): {accounts}")
    return list(accounts)


def reconnect_paper(ib, host: str = HOST, port: int = PORT, client_id: int = CLIENT_ID) -> None:
    """Connect an IB object that lost its connection, with the same checks as the first connect."""
    if host not in {"127.0.0.1", "localhost"} or port not in PAPER_PORTS:
        raise PaperGuardError(f"Refusing to connect to {host}:{port}")
    ib.connect(host, port, clientId=client_id)
    try:
        check_paper(ib, host, port)
        ib.reqMarketDataType(DELAYED)
    except BaseException:
        ib.disconnect()
        raise


def connect_paper(ib_factory=IB, host: str = HOST, port: int = PORT, client_id: int = CLIENT_ID):
    """Connect (read-write, so orders are possible) to the paper Gateway; disconnect and raise on any failed check."""
    if host not in {"127.0.0.1", "localhost"} or port not in PAPER_PORTS:
        raise PaperGuardError(f"Refusing to connect to {host}:{port}")
    ib = ib_factory()
    try:
        ib.connect(host, port, clientId=client_id)
        check_paper(ib, host, port)
        ib.reqMarketDataType(DELAYED)
    except BaseException:
        ib.disconnect()
        raise
    return ib


class Broker:
    """The only way the engine places or cancels orders."""

    def __init__(self, ib, tag: str, dry_run: bool = False, host: str = HOST, port: int = PORT, risk: Risk | None = None):
        self.ib = ib
        self.tag = tag  # orderRef put on every order, so reconcile can tell ours from anyone else's
        self.dry_run = dry_run
        self.risk = risk  # the limits outside the strategy; None only in tests and tools that must not be blocked
        self._host, self._port = host, port
        self.sent = 0  # orders this process sent to IB (a modify counts too); the proof prints it
        self.errors: dict[int, tuple[int, str]] = {}  # orderId -> (IB error code, text), the last real error for it
        ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract=None) -> None:
        """IB reports a refusal as an error on the order's id. Warnings and the cancel notice are not refusals."""
        if reqId > 0 and errorCode not in NOT_A_REFUSAL and not 2100 <= errorCode < 2200:
            self.errors[reqId] = (errorCode, errorString)

    def refused(self, trade) -> bool:
        """Did IB refuse this order? ib_async shows a refusal as Inactive for some errors and as Cancelled for others
        (a bad price increment, error 110, arrives as Cancelled), so the error recorded for the order id counts too."""
        return trade.orderStatus.status == "Inactive" or trade.order.orderId in self.errors

    def rejection(self, trade) -> str:
        """IB's code and reason for a refused order, as one line (it is logged where the refusal is found)."""
        self.ib.sleep(0.3)  # the error and the status update are separate messages: let both arrive
        code, text = self.errors.get(trade.order.orderId, (0, ""))
        if not code:  # fall back to the trade's own log
            logged = [e for e in getattr(trade, "log", []) if getattr(e, "errorCode", 0)]
            if logged:
                code, text = logged[-1].errorCode, logged[-1].message
        reason = f"[{code}] {text}" if code else "no reason given by IB"
        log.error("IB REJECTED order %s %s %s %s @ %s: %s", trade.order.orderId, trade.order.action,
                  trade.order.totalQuantity, trade.contract.symbol,
                  getattr(trade.order, "lmtPrice", 0) or getattr(trade.order, "auxPrice", 0), reason)
        return reason

    def place(self, contract, order, ref: str | None = None):
        """Check paper-ness, tag the order and send it. In dry-run mode log it and return None.

        `ref` (an entry's idempotent orderRef) must start with the tag; without it the order carries the plain tag.
        """
        if not self.ib.isConnected():
            raise PaperGuardError("Not connected")
        check_paper(self.ib, self._host, self._port)
        if ref is not None and not owns(self.tag, ref):
            raise ValueError(f"orderRef {ref!r} does not belong to {self.tag!r}")
        order.orderRef = ref or order.orderRef or self.tag
        self.check_risk(contract, order)
        if self.dry_run:
            log.info("DRY RUN: would place %s %s %s", order.action, order.totalQuantity, contract.symbol)
            return None
        self.sent += 1
        return self.ib.placeOrder(contract, order)

    def check_risk(self, contract, order) -> None:
        """Raise RiskRefused if the limits forbid this order (buys only; sells and cancels always pass)."""
        if self.risk is not None:
            self.risk.check(self.ib, contract, order)

    def cancel(self, order):
        if not self.ib.isConnected():
            raise PaperGuardError("Not connected")
        check_paper(self.ib, self._host, self._port)
        if self.dry_run:
            return None
        return self.ib.cancelOrder(order)
