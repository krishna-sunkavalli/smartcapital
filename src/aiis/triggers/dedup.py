"""Per-symbol trigger deduplication and cooldown, applied BEFORE the LLM is
invoked. An EMA cross fires once, not every polling cycle. Cooldowns are
persisted in the database so a restart mid-flow cannot re-fire suppressed
triggers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiis.db.models import TriggerEvent
from aiis.triggers.buy_triggers import Trigger


def admit_trigger(
    session: Session,
    symbol: str,
    trigger: Trigger,
    cooldown_days: int,
    now: datetime | None = None,
) -> TriggerEvent | None:
    """Persist and return the trigger event, or None if the (symbol, type)
    pair is still inside its cooldown window."""
    now = now or datetime.now(timezone.utc)
    active = session.execute(
        select(TriggerEvent)
        .where(
            TriggerEvent.symbol == symbol,
            TriggerEvent.trigger_type == trigger.trigger_type,
            TriggerEvent.cooldown_until.is_not(None),
            TriggerEvent.cooldown_until > now,
        )
        .limit(1)
    ).scalar_one_or_none()
    if active is not None:
        return None

    event = TriggerEvent(
        symbol=symbol,
        trigger_type=trigger.trigger_type,
        side=trigger.side,
        details=trigger.details,
        cooldown_until=now + timedelta(days=cooldown_days),
        status="fired",
    )
    session.add(event)
    session.flush()
    return event
