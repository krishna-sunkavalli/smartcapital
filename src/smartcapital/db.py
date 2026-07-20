"""Persisted state: one Proposal row carries the whole lifecycle (trigger ->
data packet -> LLM verdict -> approval -> order), plus cooldowns, flags, and
an append-only event log. Everything survives a restart.
"""
from __future__ import annotations

import enum
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import JSON, DateTime, Enum, Float, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from smartcapital.config import secrets


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; we store everything UTC, so re-tag."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    DECLINED = "declined"      # LLM said no; logged, nothing sent
    PENDING = "pending"        # awaiting human decision on Telegram
    APPROVED = "approved"      # human approved; not yet submitted
    DENIED = "denied"          # human denied
    EXPIRED = "expired"        # TTL elapsed unanswered
    VOIDED = "voided"          # failed a pre-submit check (e.g. price left band)
    EXECUTED = "executed"      # limit order submitted
    FILLED = "filled"
    CANCELED = "canceled"      # order canceled/expired at the broker


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    trigger_details: Mapped[dict] = mapped_column(JSON, default=dict)
    packet: Mapped[dict] = mapped_column(JSON, default=dict)          # TA + fundamentals sent to the LLM
    llm_model: Mapped[str] = mapped_column(String(64), default="")
    llm_verdict: Mapped[dict] = mapped_column(JSON, default=dict)     # recommendation, reasoning, risks
    reference_price: Mapped[float] = mapped_column(Float, default=0.0)
    limit_low: Mapped[float] = mapped_column(Float, default=0.0)
    limit_high: Mapped[float] = mapped_column(Float, default=0.0)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    notional: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nonce: Mapped[str] = mapped_column(String(64), default=new_id)    # single-use approval token
    status: Mapped[Status] = mapped_column(Enum(Status), index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Cooldown(Base):
    """(symbol, trigger_type) pairs that must not re-fire yet."""

    __tablename__ = "cooldowns"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    trigger_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Flag(Base):
    """System flags; currently just the kill switch."""

    __tablename__ = "flags"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Append-only log: every trigger, verdict, approval, and order event."""

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


_engine = None
_factory = None


def init_db(url: str | None = None):
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(url or secrets().database_url, future=True)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    init_db()
    s = _factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log(s: Session, kind: str, proposal_id: str | None = None, **payload) -> None:
    s.add(Event(kind=kind, proposal_id=proposal_id, payload=payload))


def in_cooldown(s: Session, symbol: str, trigger_type: str, now: datetime | None = None) -> bool:
    now = now or utcnow()
    row = s.get(Cooldown, (symbol, trigger_type))
    return row is not None and as_utc(row.until) > now


def start_cooldown(s: Session, symbol: str, trigger_type: str, until: datetime) -> None:
    s.merge(Cooldown(symbol=symbol, trigger_type=trigger_type, until=until))


def kill_switch_on(s: Session) -> bool:
    flag = s.get(Flag, "kill_switch")
    return flag is not None and flag.value == "on"


def set_kill_switch(s: Session, on: bool) -> None:
    s.merge(Flag(name="kill_switch", value="on" if on else "off", set_at=utcnow()))


def pending_proposals(s: Session) -> list[Proposal]:
    return list(s.execute(select(Proposal).where(Proposal.status == Status.PENDING)).scalars())


def approved_proposals(s: Session) -> list[Proposal]:
    return list(s.execute(select(Proposal).where(Proposal.status == Status.APPROVED)).scalars())
