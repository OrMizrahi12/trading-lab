"""Run IB's TOP_PERC_GAIN market scan on major US stocks (paper Gateway). Paper trading only."""

from ib_async import IB, ScannerSubscription

from market_data import DELAYED, HOST, PORT

CLIENT_ID = 4  # Must be unique per connection to the Gateway (1-3 are taken).


def show_errors(reqId, errorCode, errorString, contract):
    """Print every message IB sends back, verbatim."""
    print(f"    IB message: reqId={reqId} code={errorCode} {errorString!r}")


def top_gainers(ib, rows=10):
    """TOP_PERC_GAIN = biggest percentage price gain today. Returns IB's ScanData rows."""
    sub = ScannerSubscription(
        instrument="STK",
        locationCode="STK.US.MAJOR",  # major US exchanges
        scanCode="TOP_PERC_GAIN",
        abovePrice=5,  # skip stocks under $5
        aboveVolume=1_000_000,  # skip stocks that traded under 1 million shares today
        numberOfRows=rows,
    )
    return ib.reqScannerData(sub)


def main():
    ib = IB()
    ib.errorEvent += show_errors
    # readonly=True: the session can read data but cannot place orders.
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)
    try:
        accounts = ib.managedAccounts()
        if not all(a.startswith("DU") for a in accounts):
            raise RuntimeError(f"Non-paper account(s): {accounts}")

        ib.reqMarketDataType(DELAYED)  # fall back to delayed data where live is not subscribed
        results = top_gainers(ib)
        if not results:
            print("scanner returned no rows")
            return
        print("TOP_PERC_GAIN, STK.US.MAJOR, price > $5, volume > 1M")
        print(f"{'rank':>4}  {'symbol':<8} exchange")
        for r in results[:10]:
            c = r.contractDetails.contract
            print(f"{r.rank + 1:>4}  {c.symbol:<8} {c.primaryExchange or c.exchange}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
