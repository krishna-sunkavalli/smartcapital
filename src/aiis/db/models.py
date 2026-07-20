"""Persisted state. The process must survive a restart mid-flow, so every piece
of lifecycle state lives here, never in memory: open proposals, cooldowns,
approvals (single-use nonces), order lifecycle, and the append-only audit log.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TriggerEvent(Base):
    """A trigger firing. Deduplicated per (symbol, trigger_type) via cooldown
    BEFORE the LLM is invoked. Triggers initiate analysis; they never decide."""

    __tablename__ = "trigger_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    trigger_type: Mapped[str] = mapped_column(String(48))  # e.g. ema200_cross, weekly_review
    side: Mapped[str] = mapped_column(String(8))  # buy | review
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="fired")  # fired|analyzed|suppressed

    __table_args__ = (Index("ix_trigger_symbol_type", "symbol", "trigger_type"),)


class Recommendation(str, enum.Enum):
    BUY = "buy"
    WATCH = "watch"
    PASS = "pass"
    HOLD = "hold"
    TRIM = "trim"
    SELL = "sell"


class Analysis(Base):
    """One adversarial LLM run: bear pass, bull pass, judge pass.

    Every recommendation - including Pass and Watch - is logged with a
    hypothetical entry price so all decisions can be scored later. Model and
    prompt versions are pinned and recorded for reproducibility.
    """

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trigger_event_id: Mapped[str] = mapped_column(ForeignKey("trigger_events.id"))
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # buy | portfolio_review
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(16))
    temperature: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer, default=1)
    data_packet: Mapped[dict] = mapped_column(JSON)  # full packet incl. source + as-of per field
    bear_case: Mapped[str] = mapped_column(Text)
    bull_case: Mapped[str] = mapped_column(Text)
    judge_output: Mapped[dict] = mapped_column(JSON)
    recommendation: Mapped[Recommendation] = mapped_column(Enum(Recommendation))
    confidence_label: Mapped[str | None] = mapped_column(String(16), nullable=True)  # label, NOT probability
    hypothetical_entry_price: Mapped[float] = mapped_column(Float)
    thesis_conditions: Mapped[list] = mapped_column(JSON, default=list)  # for later thesis-break checks


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    VOIDED = "voided"  # e.g. price left the approved band pre-submission
    EXECUTED = "executed"
    FAILED = "failed"


class Proposal(Base):
    """An actionable recommendation awaiting human approval. Approval is bound
    to this specific proposal AND its price band."""

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(8))  # buy | trim | sell
    qty: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    limit_low: Mapped[float] = mapped_column(Float)
    limit_high: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ProposalStatus] = mapped_column(Enum(ProposalStatus), default=ProposalStatus.PENDING)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    guardrail_report: Mapped[dict] = mapped_column(JSON, default=dict)

    approval: Mapped[Approval | None] = relationship(back_populates="proposal", uselist=False)
    order: Mapped[OrderRecord | None] = relationship(back_populates="proposal", uselist=False)


class Approval(Base):
    """Single-use, signed, time-limited approval bound to a proposal + band."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    nonce: Mapped[str] = mapped_column(String(64), unique=True)
    message_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    buttons_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # approved|rejected|expired
    decided_by_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    typeback_required: Mapped[int] = mapped_column(Integer, default=0)
    typeback_ok: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[int] = mapped_column(Integer, default=0)  # nonce is single-use

    proposal: Mapped[Proposal] = relationship(back_populates="approval")


class OrderStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELED = "canceled"
    REJECTED = "rejected"


class OrderRecord(Base):
    """Broker order lifecycle. client_order_id makes submission idempotent:
    duplicate prevention is persisted here, not in-memory."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limit_price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(8))
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.SUBMITTED)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    transitions: Mapped[list] = mapped_column(JSON, default=list)  # every state transition, persisted

    proposal: Mapped[Proposal] = relationship(back_populates="order")


class Position(Base):
    """System-managed open position with its original thesis, so the
    thesis-break review trigger can re-verify the cited conditions."""

    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    qty: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    sector: Mapped[str] = mapped_column(String(48), default="unknown")
    origin_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Append-only audit log. Rows are only ever inserted, never updated or
    deleted (enforced by convention + audit.append being the only writer)."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemFlag(Base):
    """Kill switch, anomaly halt, base-rate alarm and similar system-wide flags."""

    __tablename__ = "system_flags"

    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
