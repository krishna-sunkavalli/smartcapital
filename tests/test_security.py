import os

os.environ.setdefault("APPROVAL_SIGNING_SECRET", "test-secret")

from smartcapital.security import make_callback, verify_callback

PID = "a" * 32
NONCE = "n" * 32


def test_roundtrip_and_size():
    data = make_callback("approve", PID, NONCE, 99.0, 101.0)
    assert len(data.encode()) <= 64  # Telegram hard limit
    assert verify_callback(data, PID, NONCE, 99.0, 101.0) == "approve"


def test_bound_to_price_band():
    data = make_callback("approve", PID, NONCE, 99.0, 101.0)
    assert verify_callback(data, PID, NONCE, 102.0, 104.0) is None


def test_bound_to_proposal_and_nonce():
    data = make_callback("approve", PID, NONCE, 99.0, 101.0)
    assert verify_callback(data, "b" * 32, NONCE, 99.0, 101.0) is None
    assert verify_callback(data, PID, "x" * 32, 99.0, 101.0) is None


def test_decision_tamper_rejected():
    data = make_callback("deny", PID, NONCE, 99.0, 101.0)
    forged = "approve" + data[len("deny"):]
    assert verify_callback(forged, PID, NONCE, 99.0, 101.0) is None
