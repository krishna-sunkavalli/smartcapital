from datetime import datetime, timedelta, timezone

from aiis.approval import security
from aiis.approval.fatigue import buttons_active, needs_typeback, weekly_digest
from aiis.config import ApprovalCfg
from aiis.db.models import Approval


PID = "a" * 32
NONCE = "n" * 24


def test_callback_roundtrip():
    data = security.make_callback("approve", PID, NONCE, 99.0, 101.0)
    assert len(data.encode()) <= 64  # Telegram callback_data hard limit
    assert security.verify_callback(data, PID, NONCE, 99.0, 101.0) == "approve"


def test_callback_bound_to_price_band():
    data = security.make_callback("approve", PID, NONCE, 99.0, 101.0)
    # Same proposal, drifted band -> approval no longer valid
    assert security.verify_callback(data, PID, NONCE, 102.0, 104.0) is None


def test_callback_bound_to_proposal_and_nonce():
    data = security.make_callback("approve", PID, NONCE, 99.0, 101.0)
    assert security.verify_callback(data, "b" * 32, NONCE, 99.0, 101.0) is None
    assert security.verify_callback(data, PID, "x" * 24, 99.0, 101.0) is None


def test_tampered_decision_rejected():
    data = security.make_callback("reject", PID, NONCE, 99.0, 101.0)
    forged = "approve" + data[len("reject"):]
    assert security.verify_callback(forged, PID, NONCE, 99.0, 101.0) is None


def test_read_delay_blocks_reflex_taps():
    cfg = ApprovalCfg(min_read_delay_seconds=45)
    now = datetime.now(timezone.utc)
    ap = Approval(proposal_id=PID, nonce=NONCE, message_sent_at=now - timedelta(seconds=10))
    assert not buttons_active(ap, cfg, now)
    ap.message_sent_at = now - timedelta(seconds=46)
    assert buttons_active(ap, cfg, now)


def test_typeback_threshold():
    cfg = ApprovalCfg(typeback_notional_threshold=1000.0)
    assert not needs_typeback(999.0, cfg)
    assert needs_typeback(1000.0, cfg)


def test_weekly_digest_gate_alarm(session):
    now = datetime.now(timezone.utc)
    for i in range(6):
        session.add(Approval(proposal_id=f"p{i:031d}", nonce=f"nn{i:022d}",
                             decision="approved", decided_at=now - timedelta(days=1),
                             consumed=1))
    session.flush()
    d = weekly_digest(session, ApprovalCfg(), now=now)
    assert d["approval_rate"] == 1.0
    assert d["gate_alarm"] is True
