"""Approval-token signing. Telegram callback data is untrusted input, so the
approve/deny payload is HMAC-signed and bound to one specific proposal, its
single-use nonce, and its price band.
"""
from __future__ import annotations

import hashlib
import hmac

from smartcapital.config import secrets


def _mac(payload: str) -> str:
    key = secrets().approval_signing_secret.encode()
    if not key:
        raise RuntimeError("APPROVAL_SIGNING_SECRET is not set")
    # Telegram caps callback_data at 64 bytes; 12 hex chars (48 bits) of tag is
    # plenty for a single-use, server-side-checked token.
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:12]


def make_callback(decision: str, proposal_id: str, nonce: str,
                  limit_low: float, limit_high: float) -> str:
    signed = f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{limit_low:.2f}:{limit_high:.2f}"
    return f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{_mac(signed)}"


def verify_callback(data: str, proposal_id: str, nonce: str,
                    limit_low: float, limit_high: float) -> str | None:
    try:
        decision, pid, non, tag = data.split(":")
    except ValueError:
        return None
    if pid != proposal_id[:12] or non != nonce[:16]:
        return None
    expected = _mac(f"{decision}:{proposal_id[:12]}:{nonce[:16]}:{limit_low:.2f}:{limit_high:.2f}")
    if not hmac.compare_digest(tag, expected):
        return None
    return decision if decision in ("approve", "deny") else None
