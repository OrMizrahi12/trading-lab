"""Connect to TWS (paper) and print the account summary. Paper trading only."""

from ib_async import IB

HOST = "127.0.0.1"
PORT = 7497  # TWS paper port. Live (7496) is forbidden in this repo.
PAPER_PORTS = {7497, 4002}

assert PORT in PAPER_PORTS, f"Refusing to connect: port {PORT} is not a paper port"

ib = IB()
ib.connect(HOST, PORT, clientId=1)

try:
    accounts = ib.managedAccounts()
    assert all(a.startswith("DU") for a in accounts), f"Non-paper account(s): {accounts}"

    for item in ib.accountSummary():
        print(f"{item.account}  {item.tag}: {item.value} {item.currency}")
finally:
    ib.disconnect()
