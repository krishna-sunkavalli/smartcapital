"""In-memory state for the running process: proposals, cooldowns, and a plain
event log. State lives for the lifetime of `smartcapital run`.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


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


@dataclass
class Proposal:
    symbol: str
    trigger_type: str
    trigger_details: dict
    packet: dict
    llm_model: str
    llm_verdict: dict
    reference_price: float
    limit_low: float
    limit_high: float
    qty: float
    notional: float
    status: Status
    expires_at: datetime | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None
    status_reason: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None


class Store:
    def __init__(self) -> None:
        self.proposals: dict[str, Proposal] = {}
        self.cooldowns: dict[tuple[str, str], datetime] = {}
        self.events: list[dict] = []
        self.analyses_by_day: dict[str, int] = {}

    # --- daily analysis budget -------------------------------------------
    def analyses_today(self, now: datetime | None = None) -> int:
        return self.analyses_by_day.get((now or utcnow()).date().isoformat(), 0)

    def record_analysis(self, now: datetime | None = None) -> None:
        key = (now or utcnow()).date().isoformat()
        self.analyses_by_day[key] = self.analyses_by_day.get(key, 0) + 1

    # --- proposals ---------------------------------------------------------
    def add(self, p: Proposal) -> Proposal:
        self.proposals[p.id] = p
        return p

    def get(self, proposal_id: str) -> Proposal | None:
        return self.proposals.get(proposal_id)

    def with_status(self, status: Status) -> list[Proposal]:
        return [p for p in self.proposals.values() if p.status is status]

    # --- cooldowns ---------------------------------------------------------
    def in_cooldown(self, symbol: str, trigger_type: str, now: datetime | None = None) -> bool:
        until = self.cooldowns.get((symbol, trigger_type))
        return until is not None and until > (now or utcnow())

    def start_cooldown(self, symbol: str, trigger_type: str, until: datetime) -> None:
        self.cooldowns[(symbol, trigger_type)] = until

    # --- event log ---------------------------------------------------------
    def log(self, kind: str, proposal_id: str | None = None, **payload) -> None:
        self.events.append({"at": utcnow().isoformat(), "kind": kind,
                            "proposal_id": proposal_id, **payload})
