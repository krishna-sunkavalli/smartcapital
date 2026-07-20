"""Approval-fatigue countermeasures. The human gate is only worth having if it
keeps functioning; these controls keep ownership real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aiis.config import ApprovalCfg, RolloutCfg
from aiis.db.models import Approval


def approvals_today(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return session.execute(
        select(func.count(Approval.id)).where(
            Approval.decision == "approved", Approval.decided_at >= start)
    ).scalar_one()


def daily_cap_reached(session: Session, cfg: ApprovalCfg, rollout: RolloutCfg,
                      now: datetime | None = None) -> bool:
    cap = min(cfg.daily_approval_cap,
              rollout.phase2_max_daily_approvals if rollout.phase == 2 else cfg.daily_approval_cap)
    return approvals_today(session, now) >= cap


def buttons_active(approval: Approval, cfg: ApprovalCfg, now: datetime | None = None) -> bool:
    """Mandatory delay between message delivery and button activation - forces
    reading, not reflex-tapping."""
    now = now or datetime.now(timezone.utc)
    if approval.message_sent_at is None:
        return False
    return now >= approval.message_sent_at + timedelta(seconds=cfg.min_read_delay_seconds)


def needs_typeback(notional: float, cfg: ApprovalCfg) -> bool:
    return notional >= cfg.typeback_notional_threshold


def weekly_digest(session: Session, cfg: ApprovalCfg, now: datetime | None = None) -> dict:
    """Approval-rate digest. A near-100% approval rate is treated as a signal
    that the human gate has stopped functioning, not as success."""
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    rows = session.execute(
        select(Approval.decision).where(Approval.decided_at >= week_ago,
                                        Approval.decision.is_not(None))
    ).scalars().all()
    total = len(rows)
    approved = sum(1 for d in rows if d == "approved")
    rate = approved / total if total else 0.0
    return {
        "week_decisions": total,
        "approved": approved,
        "rejected": sum(1 for d in rows if d == "rejected"),
        "expired": sum(1 for d in rows if d == "expired"),
        "approval_rate": rate,
        "gate_alarm": total >= 5 and rate >= cfg.approval_rate_alarm,
    }
