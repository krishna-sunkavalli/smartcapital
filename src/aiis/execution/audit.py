"""Append-only audit log. This module is the only writer; rows are inserted,
never updated or deleted. Records the complete data packet, all three LLM
passes, the approval event, and the order result for every flow.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from aiis.db.models import AuditEvent


def append_audit(session: Session, kind: str, ref_id: str | None, payload: dict) -> None:
    session.add(AuditEvent(kind=kind, ref_id=ref_id, payload=payload))
