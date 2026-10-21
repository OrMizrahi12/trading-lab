"""Print the IB Gateway (paper) account summary using the official ibapi client. Paper trading only."""

import threading

from ibapi.account_summary_tags import AccountSummaryTags
from ibapi.client import EClient
from ibapi.wrapper import EWrapper

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 2  # Must be unique per connection to the Gateway (connect_test.py uses 1).
TIMEOUT = 15  # Seconds before giving up and disconnecting.

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")


class Hello(EWrapper, EClient):
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self.accounts = []
        self.done = threading.Event()

    def managedAccounts(self, accountsList):
        self.accounts = [a for a in accountsList.split(",") if a]

    def nextValidId(self, orderId):
        if not self.accounts or not all(a.startswith("DU") for a in self.accounts):
            print(f"Non-paper account(s): {self.accounts}")
            self.done.set()
            return
        self.reqAccountSummary(9001, "All", AccountSummaryTags.AllTags)

    def accountSummary(self, reqId, account, tag, value, currency):
        print(f"{account}  {tag}: {value} {currency}")

    def accountSummaryEnd(self, reqId):
        self.cancelAccountSummary(reqId)
        self.done.set()

    def error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"error {errorCode}: {errorString}")


app = Hello()
app.connect(HOST, PORT, CLIENT_ID)
# The reader loop runs in a thread; disconnecting from inside it crashes ibapi's run().
threading.Thread(target=app.run, daemon=True).start()
app.done.wait(TIMEOUT)
app.disconnect()
