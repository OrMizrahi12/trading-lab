"""The live engine refuses anything but a local paper Gateway and a DU account."""

from types import SimpleNamespace

import pytest

from live.broker import Broker, PaperGuardError, check_paper, connect_paper
from tests.fake_ib import FakeIB


def order(action="BUY", qty=10):
    return SimpleNamespace(action=action, totalQuantity=qty, orderRef="")


CONTRACT = SimpleNamespace(symbol="QQQ")


def test_connect_paper_accepts_du_account_and_is_not_readonly():
    ib = connect_paper(ib_factory=lambda: FakeIB(["DU1234567"]))
    assert ib.isConnected()
    assert ib.connect_args[3].get("readonly") is None  # orders must be possible
    assert ib.market_data_type == 3


@pytest.mark.parametrize("accounts", [[], ["U1234567"], ["DU1234567", "U7654321"]])
def test_connect_paper_refuses_bad_accounts_and_disconnects(accounts):
    fake = FakeIB(accounts)
    with pytest.raises(PaperGuardError):
        connect_paper(ib_factory=lambda: fake)
    assert not fake.isConnected()


@pytest.mark.parametrize("port", [1, 80, 4003])
def test_connect_refuses_non_paper_port_before_connecting(port):
    fake = FakeIB()
    with pytest.raises(PaperGuardError):
        connect_paper(ib_factory=lambda: fake, port=port)
    assert fake.connect_args is None


def test_connect_refuses_remote_host():
    fake = FakeIB()
    with pytest.raises(PaperGuardError):
        connect_paper(ib_factory=lambda: fake, host="10.0.0.5")
    assert fake.connect_args is None


def test_broker_tags_and_places_on_paper():
    ib = connect_paper(ib_factory=FakeIB)
    broker = Broker(ib, "live:ema_trend:QQQ")
    broker.place(CONTRACT, order())
    assert [o.orderRef for _, o in ib.placed] == ["live:ema_trend:QQQ"]


def test_broker_does_not_place_when_account_turns_non_paper():
    ib = connect_paper(ib_factory=FakeIB)
    broker = Broker(ib, "live:x:QQQ")
    ib.accounts = ["U1234567"]
    with pytest.raises(PaperGuardError):
        broker.place(CONTRACT, order())
    with pytest.raises(PaperGuardError):
        broker.cancel(order())
    assert ib.placed == [] and ib.cancelled == []


def test_broker_refuses_when_disconnected():
    ib = connect_paper(ib_factory=FakeIB)
    broker = Broker(ib, "live:x:QQQ")
    ib.disconnect()
    with pytest.raises(PaperGuardError):
        broker.place(CONTRACT, order())
    assert ib.placed == []


def test_dry_run_checks_the_account_but_sends_nothing():
    ib = connect_paper(ib_factory=FakeIB)
    broker = Broker(ib, "live:x:QQQ", dry_run=True)
    assert broker.place(CONTRACT, order()) is None
    assert ib.placed == []
    ib.accounts = ["U1234567"]
    with pytest.raises(PaperGuardError):
        broker.place(CONTRACT, order())


def test_check_paper_returns_accounts():
    assert check_paper(FakeIB(["DU1", "DU2"])) == ["DU1", "DU2"]
