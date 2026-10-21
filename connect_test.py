"""Connect to TWS (paper) and print the account summary. Paper trading only."""

import argparse

from ib_async import IB

HOST = "127.0.0.1"
PORT = 7497  # TWS paper port. Live (7496) is forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 1  # Must be unique per connection to TWS.

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="print host, port and client ID without connecting",
)
args = parser.parse_args()

if args.dry_run:
    print(f"host={HOST} port={PORT} client_id={CLIENT_ID} (dry run, not connecting)")
    raise SystemExit(0)

ib = IB()
# readonly=True: the session can read data but cannot place orders.
ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)

try:
    accounts = ib.managedAccounts()
    if not all(a.startswith("DU") for a in accounts):
        raise RuntimeError(f"Non-paper account(s): {accounts}")

    for item in ib.accountSummary():
        print(f"{item.account}  {item.tag}: {item.value} {item.currency}")
finally:
    ib.disconnect()
