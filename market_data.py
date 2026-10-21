"""Qualify contracts and request snapshot quotes from IB Gateway (paper). Paper trading only."""

from ib_async import IB, Forex, Stock

HOST = "127.0.0.1"
PORT = 4002  # IB Gateway paper port. Live ports are forbidden in this repo.
PAPER_PORTS = {7497, 4002}
CLIENT_ID = 3  # Must be unique per connection to the Gateway.

LIVE, DELAYED = 1, 3  # IB market data types
SNAPSHOT_WAIT = 5  # seconds to wait for IB to answer each snapshot

if PORT not in PAPER_PORTS:
    raise RuntimeError(f"Refusing to connect: port {PORT} is not a paper port")


def show_errors(reqId, errorCode, errorString, contract):
    """Print every message IB sends back, verbatim."""
    print(f"    IB message: reqId={reqId} code={errorCode} {errorString!r}")


def qualify(ib):
    contracts = [
        Stock("AAPL", "SMART", "USD"),
        Stock("SPY", "SMART", "USD"),
        Forex("EURUSD"),  # IDEALPRO is the forex venue
    ]
    ib.qualifyContracts(*contracts)
    for c in contracts:
        print(f"{c.symbol}/{c.currency} {c.secType}: conId={c.conId} primaryExchange={c.primaryExchange!r} exchange={c.exchange}")
    return contracts


def snapshot(ib, contracts, market_data_type, label):
    print(f"\n--- {label} (market data type {market_data_type}) ---")
    ib.reqMarketDataType(market_data_type)
    for c in contracts:
        print(f"{c.symbol}{c.currency if c.secType == 'CASH' else ''}:")
        ticker = ib.reqMktData(c, genericTickList="", snapshot=True)
        ib.sleep(SNAPSHOT_WAIT)
        print(
            f"    bid={ticker.bid} ask={ticker.ask} last={ticker.last} close={ticker.close} "
            f"marketDataType={ticker.marketDataType}"
        )
        ib.cancelMktData(c)


ib = IB()
ib.errorEvent += show_errors
# readonly=True: the session can read data but cannot place orders.
ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)

try:
    accounts = ib.managedAccounts()
    if not all(a.startswith("DU") for a in accounts):
        raise RuntimeError(f"Non-paper account(s): {accounts}")

    contracts = qualify(ib)
    snapshot(ib, contracts, LIVE, "live")
    snapshot(ib, contracts, DELAYED, "delayed")
finally:
    ib.disconnect()
