"""Kill switch: a single command disables all proposals immediately. State is
persisted so it survives restarts, and it is read into every GuardrailContext.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from aiis.db.models import SystemFlag, utcnow

FLAG = "kill_switch"


def activate(session: Session, reason: str = "manual") -> None:
    session.merge(SystemFlag(name=FLAG, value="on", set_at=utcnow(), reason=reason))


def deactivate(session: Session) -> None:
    session.merge(SystemFlag(name=FLAG, value="off", set_at=utcnow(), reason="manual clear"))


def is_active(session: Session) -> bool:
    flag = session.get(SystemFlag, FLAG)
    return flag is not None and flag.value == "on"
