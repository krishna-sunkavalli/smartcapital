"""Execution: refresh -> re-check ALL guardrails -> idempotent limit order ->
lifecycle tracking. Approval is necessary but not sufficient; the same
deterministic checks that ran at proposal time run again here against fresh
data, and a price outside the approved band VOIDS the proposal rather than
resubmitting it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest
from sqlalchemy.orm import Session

from aiis.config import AppConfig, secrets
from aiis.db.models import OrderRecord, OrderStatus, Position, Proposal, ProposalStatus
from aiis.execution.audit import append_audit
from aiis.guardrails.engine import GuardrailContext, ProposedOrder, check_all

log = logging.getLogger(__name__)


def trading_client() -> TradingClient:
    s = secrets()
    return TradingClient(s.alpaca_api_key, s.alpaca_secret_key, paper=s.alpaca_env != "live")


def execute_approved_proposal(
    session: Session,
    proposal: Proposal,
    ctx: GuardrailContext,
    cfg: AppConfig,
    client: TradingClient | None = None,
) -> OrderRecord | None:
    """Submit the approved proposal if and only if every guardrail still
    passes against the refreshed context. Idempotent: the client order id is
    derived from the proposal id and persisted, so a crash after submission
    cannot cause a duplicate on retry."""
    if proposal.status is not ProposalStatus.APPROVED:
        return None

    # Idempotency: if an order row already exists, we already submitted
    # (possibly crashed before recording the broker response) - never resubmit.
    if proposal.order is not None:
        log.info("proposal %s already has order %s; skipping", proposal.id,
                 proposal.order.client_order_id)
        return proposal.order

    sector = "unknown"
    pos = session.get(Position, proposal.symbol)
    if pos is not None:
        sector = pos.sector

    order = ProposedOrder(
        symbol=proposal.symbol,
        action=proposal.action,
        qty=proposal.qty,
        notional=proposal.notional,
        reference_price=proposal.reference_price,
        limit_low=proposal.limit_low,
        limit_high=proposal.limit_high,
        sector=sector,
    )
    violations = check_all(order, ctx, cfg)
    if violations:
        # Price left the band, exposure changed, kill switch flipped, ... :
        # the proposal is voided, not resubmitted.
        proposal.status = ProposalStatus.VOIDED
        proposal.status_reason = "; ".join(f"{v.rule}: {v.detail}" for v in violations)
        append_audit(session, "proposal_voided_pre_execution", proposal.id,
                     {"violations": [vars(v) for v in violations]})
        return None

    side = OrderSide.BUY if proposal.action == "buy" else OrderSide.SELL
    # Buys price at the top of the approved band, sells at the bottom: still
    # marketable at the current in-band price, never worse than approved.
    limit_price = round(proposal.limit_high if side == OrderSide.BUY else proposal.limit_low, 2)
    client_order_id = f"aiis-{proposal.id}"

    record = OrderRecord(
        proposal_id=proposal.id,
        client_order_id=client_order_id,
        limit_price=limit_price,
        qty=proposal.qty,
        side=side.value,
        status=OrderStatus.SUBMITTED,
        transitions=[{"at": datetime.now(timezone.utc).isoformat(), "to": "submitting"}],
    )
    session.add(record)
    session.flush()  # persist intent BEFORE the network call

    client = client or trading_client()
    resp = client.submit_order(LimitOrderRequest(
        symbol=proposal.symbol,
        qty=proposal.qty,
        side=side,
        limit_price=limit_price,
        time_in_force=TimeInForce(cfg.orders.order_time_in_force.upper()
                                  if cfg.orders.order_time_in_force.isupper()
                                  else cfg.orders.order_time_in_force),
        client_order_id=client_order_id,
    ))
    record.broker_order_id = str(resp.id)
    record.submitted_at = datetime.now(timezone.utc)
    record.transitions = record.transitions + [
        {"at": record.submitted_at.isoformat(), "to": "submitted", "broker_id": str(resp.id)}]
    proposal.status = ProposalStatus.EXECUTED
    append_audit(session, "order_submitted", proposal.id, {
        "client_order_id": client_order_id, "broker_order_id": str(resp.id),
        "limit_price": limit_price, "qty": proposal.qty, "side": side.value,
    })
    return record


_TERMINAL = {OrderStatus.FILLED, OrderStatus.EXPIRED, OrderStatus.CANCELED, OrderStatus.REJECTED}

_BROKER_MAP = {
    "new": OrderStatus.SUBMITTED, "accepted": OrderStatus.SUBMITTED,
    "partially_filled": OrderStatus.PARTIAL, "filled": OrderStatus.FILLED,
    "expired": OrderStatus.EXPIRED, "canceled": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED, "done_for_day": OrderStatus.EXPIRED,
}


def sync_order_lifecycles(session: Session, client: TradingClient | None = None) -> int:
    """Poll open orders and persist every state transition to fill, partial
    fill, or expiry. Updates the Position table on fills."""
    client = client or trading_client()
    n = 0
    for record in session.query(OrderRecord).filter(OrderRecord.status.notin_(_TERMINAL)):
        broker = client.get_order_by_client_id(record.client_order_id)
        new_status = _BROKER_MAP.get(str(broker.status.value), record.status)
        filled_qty = float(broker.filled_qty or 0)
        if new_status == record.status and filled_qty == record.filled_qty:
            continue
        record.transitions = record.transitions + [{
            "at": datetime.now(timezone.utc).isoformat(),
            "to": new_status.value, "filled_qty": filled_qty,
            "filled_avg_price": float(broker.filled_avg_price or 0) or None,
        }]
        record.status = new_status
        record.filled_qty = filled_qty
        record.filled_avg_price = float(broker.filled_avg_price or 0) or None
        if new_status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and filled_qty > 0:
            _apply_fill(session, record)
        append_audit(session, "order_transition", record.proposal_id,
                     {"client_order_id": record.client_order_id, "status": new_status.value,
                      "filled_qty": filled_qty})
        n += 1
    return n


def _apply_fill(session: Session, record: OrderRecord) -> None:
    proposal = record.proposal
    pos = session.get(Position, proposal.symbol)
    if record.side == "buy":
        if pos is None:
            session.add(Position(symbol=proposal.symbol, qty=record.filled_qty,
                                 avg_entry_price=record.filled_avg_price or record.limit_price,
                                 origin_analysis_id=proposal.analysis_id))
        else:
            total_cost = pos.qty * pos.avg_entry_price + record.filled_qty * (
                record.filled_avg_price or record.limit_price)
            pos.qty += record.filled_qty
            pos.avg_entry_price = total_cost / pos.qty
    else:
        if pos is not None:
            pos.qty = max(0.0, pos.qty - record.filled_qty)
            if pos.qty <= 1e-9:
                pos.closed_at = datetime.now(timezone.utc)
