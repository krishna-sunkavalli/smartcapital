"""Approval security primitives.

- Callback payloads are HMAC-signed with a local secret; Telegram callback
  data is treated as untrusted input.
- Approvals are single-use nonces, persisted in the Approval row.
- An approval is bound to the specific proposal AND its price band: the signed
  payload includes proposal id, action, and the band, so 'approve MSFT' cannot
  be replayed against a different proposal or a drifted band.
- Time-limited: expiry or rejection causes no action.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets

from aiis.config import secrets


def new_nonce() -> str:
    return pysecrets.token_urlsafe(24)


def _mac(payload: str) -> str:
    key = secrets().approval_signing_secret.encode()
    if not key:
        raise RuntimeError("APPROVAL_SIGNING_SECRET is not set; refusing to sign approvals")
    # Telegram callback_data is capped at 64 bytes, so truncate the tag;
    # 12 hex chars = 48 bits, ample for a single-use nonce with server-side state.
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:12]


def make_callback(decision: str, proposal_id: str, nonce: str, limit_low: float, limit_high: float) -> str:
    payload = f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{limit_low:.2f}:{limit_high:.2f}"
    # Band values participate in the MAC but are dropped from the wire payload
    # to fit 64 bytes; verification re-derives them from the DB row.
    return f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{_mac(payload)}"


def verify_callback(data: str, proposal_id: str, nonce: str, limit_low: float, limit_high: float) -> str | None:
    """Return the decision ('approve'/'reject') if the callback verifies
    against this exact proposal, nonce, and price band; otherwise None."""
    try:
        decision, pid, non, tag = data.split(":")
    except ValueError:
        return None
    if pid != proposal_id[:12] or non != nonce[:16]:
        return None
    expected = _mac(f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{limit_low:.2f}:{limit_high:.2f}")
    if not hmac.compare_digest(tag, expected):
        return None
    return decision if decision in ("approve", "reject") else None
