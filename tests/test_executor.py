"""Tests for order placement: the pre-submit gate, atomic claim, and the
submit-failure rollback that must never strand an approved order."""
from types import SimpleNamespace

from smartcapital.config import Config
from smartcapital.executor import execute, sync_orders
from smartcapital.state import Proposal, Status, Store


def make_proposal(**kw) -> Proposal:
    defaults = dict(
        symbol="AAPL", trigger_type="down_day", trigger_details={}, packet={},
        llm_model="m", llm_verdict={}, reference_price=100.0,
        limit_low=99.0, limit_high=101.0, qty=1.0, notional=100.0,
        status=Status.APPROVED,
    )
    defaults.update(kw)
    return Proposal(**defaults)


class FakeTrading:
    def __init__(self, submit=None):
        self._submit = submit
        self.submitted = []

    def submit_order(self, req):
        self.submitted.append(req)
        if isinstance(self._submit, Exception):
            raise self._submit
        return self._submit


class FakeMarket:
    def __init__(self, is_open=True, price=100.0, cash=100_000.0, trading=None):
        self._open = is_open
        self._price = price
        self._cash = cash
        self.trading = trading or FakeTrading()

    def market_open(self):
        return self._open

    def latest_price(self, _symbol):
        return self._price

    def cash(self):
        return self._cash


def _store(tmp_path) -> Store:
    return Store(path=tmp_path / "state.json")


def test_successful_submit_marks_executed(tmp_path):
    store = _store(tmp_path)
    p = store.add(make_proposal())
    market = FakeMarket(trading=FakeTrading(submit=SimpleNamespace(id="broker-1")))

    assert execute(store, p, market, Config()) is True
    assert p.status is Status.EXECUTED
    assert p.client_order_id == f"smartcap-{p.id}"
    assert p.broker_order_id == "broker-1"


def test_submit_failure_rolls_back_and_leaves_no_client_order_id(tmp_path):
    store = _store(tmp_path)
    p = store.add(make_proposal())
    market = FakeMarket(trading=FakeTrading(submit=RuntimeError("broker down")))

    assert execute(store, p, market, Config()) is False
    # Must be retryable next cycle, not stranded.
    assert p.status is Status.APPROVED
    assert p.client_order_id is None


def test_price_outside_band_voids(tmp_path):
    store = _store(tmp_path)
    p = store.add(make_proposal(limit_low=99.0, limit_high=101.0))
    market = FakeMarket(price=105.0)

    assert execute(store, p, market, Config()) is False
    assert p.status is Status.VOIDED


def test_never_resubmits_already_submitted(tmp_path):
    store = _store(tmp_path)
    p = store.add(make_proposal(client_order_id="smartcap-x"))
    market = FakeMarket(trading=FakeTrading(submit=SimpleNamespace(id="broker-2")))

    assert execute(store, p, market, Config()) is False
    assert market.trading.submitted == []


def test_only_acts_on_approved(tmp_path):
    store = _store(tmp_path)
    p = store.add(make_proposal(status=Status.PENDING))
    market = FakeMarket()

    assert execute(store, p, market, Config()) is False


class MappedTrading:
    """get_order_by_client_id returns/raises per client_order_id."""

    def __init__(self, orders):
        self.orders = orders

    def get_order_by_client_id(self, coid):
        val = self.orders[coid]
        if isinstance(val, Exception):
            raise val
        return val


def test_sync_orders_one_broker_error_does_not_abort_the_rest(tmp_path):
    store = _store(tmp_path)
    bad = store.add(make_proposal(symbol="BAD", status=Status.EXECUTED,
                                  client_order_id="smartcap-bad"))
    good = store.add(make_proposal(symbol="GOOD", status=Status.EXECUTED,
                                   client_order_id="smartcap-good"))
    filled = SimpleNamespace(status=SimpleNamespace(value="filled"),
                             filled_qty="1", filled_avg_price="100.5")
    market = FakeMarket(trading=MappedTrading({
        "smartcap-bad": RuntimeError("boom"),
        "smartcap-good": filled,
    }))

    changes = sync_orders(store, market)

    assert good.status is Status.FILLED
    assert bad.status is Status.EXECUTED  # untouched, will be retried
    assert [c[0] for c in changes] == ["GOOD"]
