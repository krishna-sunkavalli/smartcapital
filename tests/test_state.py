"""In-flight EXECUTED orders must survive a restart so their fill is still
tracked and notified; everything else stays in-memory as before."""
from smartcapital.state import Proposal, Status, Store


def _executed(**kw) -> Proposal:
    defaults = dict(
        symbol="AAPL", trigger_type="down_day", trigger_details={}, packet={"big": "x"},
        llm_model="m", llm_verdict={"recommendation": "buy"}, reference_price=100.0,
        limit_low=99.0, limit_high=101.0, qty=5.0, notional=500.0,
        status=Status.APPROVED,
    )
    defaults.update(kw)
    return Proposal(**defaults)


def test_executed_order_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    store = Store(path=path)
    p = store.add(_executed())
    assert store.transition(p, Status.APPROVED, Status.EXECUTED)
    store.mark_submitted(p, "smartcap-abc", "broker-1")

    # New process on the same state file.
    reloaded = Store(path=path)
    (recovered,) = reloaded.with_status(Status.EXECUTED)
    assert recovered.id == p.id
    assert recovered.symbol == "AAPL"
    assert recovered.client_order_id == "smartcap-abc"
    assert recovered.broker_order_id == "broker-1"
    assert recovered.notional == 500.0


def test_client_order_id_is_recoverable_from_deterministic_scheme(tmp_path):
    # If the process died before mark_submitted recorded the id, the stub still
    # rebuilds it deterministically from the proposal id.
    path = tmp_path / "state.json"
    store = Store(path=path)
    p = store.add(_executed())
    assert store.transition(p, Status.APPROVED, Status.EXECUTED)  # persists w/o coid

    reloaded = Store(path=path)
    (recovered,) = reloaded.with_status(Status.EXECUTED)
    assert recovered.client_order_id == f"smartcap-{p.id}"


def test_terminal_order_is_not_persisted(tmp_path):
    path = tmp_path / "state.json"
    store = Store(path=path)
    p = store.add(_executed())
    assert store.transition(p, Status.APPROVED, Status.EXECUTED)
    store.mark_submitted(p, "smartcap-xyz", "broker-2")
    assert store.transition(p, Status.EXECUTED, Status.FILLED)  # leaves open set

    reloaded = Store(path=path)
    assert reloaded.with_status(Status.EXECUTED) == []
    assert reloaded.with_status(Status.FILLED) == []  # filled stubs aren't kept


def test_pending_proposal_is_not_persisted(tmp_path):
    path = tmp_path / "state.json"
    store = Store(path=path)
    store.add(_executed(status=Status.PENDING))

    reloaded = Store(path=path)
    assert reloaded.proposals == {}
