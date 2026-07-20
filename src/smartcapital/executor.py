"""Order placement. Approval is necessary but not sufficient - before
submitting we re-check: kill switch off, market open, cash available, and the
live price still inside the approved band (outside = VOID, never resubmit).
Limit orders only; the client order id is the proposal id, persisted before
the network call, so a crash/retry can never double-submit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest
from sqlalchemy.orm import Session

from smartcapital import db
from smartcapital.config import Config
from smartcapital.db import Proposal, Status
from smartcapital.market import Market

log = logging.getLogger(__name__)


def execute(s: Session, p: Proposal, market: Market, cfg: Config) -> bool:
    if p.status is not Status.APPROVED:
        return False
    if p.client_order_id:  # already submitted (possibly crashed mid-flow): never resubmit
        return False

    if db.kill_switch_on(s):
        return _void(s, p, "kill switch active")
    if not market.market_open():
        return False  # not voided - retried next cycle while approval is fresh
    live = market.latest_price(p.symbol)
    if not (p.limit_low <= live <= p.limit_high):
        return _void(s, p, f"price {live} left approved band [{p.limit_low}, {p.limit_high}]")
    if market.cash() - p.notional < cfg.order.min_cash_buffer_usd:
        return _void(s, p, "insufficient cash above buffer")

    p.client_order_id = f"smartcap-{p.id}"  # persisted BEFORE the network call
    s.flush()
    resp = market.trading.submit_order(LimitOrderRequest(
        symbol=p.symbol, qty=p.qty, side=OrderSide.BUY,
        limit_price=round(p.limit_high, 2),  # never worse than the approved band top
        time_in_force=TimeInForce.DAY, client_order_id=p.client_order_id))
    p.broker_order_id = str(resp.id)
    p.status = Status.EXECUTED
    db.log(s, "order_submitted", p.id, broker_order_id=str(resp.id),
           limit_price=round(p.limit_high, 2), qty=p.qty)
    return True


def _void(s: Session, p: Proposal, reason: str) -> bool:
    p.status = Status.VOIDED
    p.status_reason = reason
    db.log(s, "proposal_voided", p.id, reason=reason)
    return False


_BROKER_TERMINAL = {"filled": Status.FILLED, "canceled": Status.CANCELED,
                    "expired": Status.CANCELED, "rejected": Status.CANCELED}


def sync_orders(s: Session, market: Market) -> list[tuple[str, str]]:
    """Track submitted orders to their end state. Returns (symbol, outcome)
    transitions for user notification."""
    changes = []
    for p in s.query(Proposal).filter(Proposal.status == Status.EXECUTED):
        order = market.trading.get_order_by_client_id(p.client_order_id)
        outcome = _BROKER_TERMINAL.get(str(order.status.value))
        if outcome:
            p.status = outcome
            p.status_reason = (f"filled {order.filled_qty} @ {order.filled_avg_price}"
                               if outcome is Status.FILLED else str(order.status.value))
            db.log(s, "order_" + outcome.value, p.id, detail=p.status_reason,
                   at=datetime.now(timezone.utc).isoformat())
            changes.append((p.symbol, p.status_reason))
    return changes
