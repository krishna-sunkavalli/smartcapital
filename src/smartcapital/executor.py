"""Order placement. Before submitting an approved proposal we re-check that
the market is open, cash is sufficient, and the live price is still inside the
approved band (outside = VOID, never resubmit). Limit orders only; the client
order id is derived from the proposal id so a retry can't double-submit.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from smartcapital.config import Config
from smartcapital.market import Market
from smartcapital.state import Proposal, Status, Store, utcnow

log = logging.getLogger(__name__)


def execute(store: Store, p: Proposal, market: Market, cfg: Config) -> bool:
    if p.status is not Status.APPROVED:
        return False
    if p.client_order_id:  # already submitted: never resubmit
        return False

    # Stale approvals must never execute on a day-old thesis: if the approval is
    # older than the execute TTL (e.g. approved just before close, unfilled next
    # session), void it instead of placing the order.
    ttl = timedelta(minutes=cfg.approval.execute_ttl_minutes)
    if p.decided_at and utcnow() - p.decided_at > ttl:
        return _void(store, p, "approval expired before execution")

    if not market.market_open():
        return False  # not voided - retried next cycle while approval is fresh
    live = market.latest_price(p.symbol)
    if not (p.limit_low <= live <= p.limit_high):
        return _void(store, p, f"price {live} left approved band [{p.limit_low}, {p.limit_high}]")
    # Reserve cash for orders already submitted but not yet filled: Alpaca's
    # `cash` doesn't drop until fill, so without this several approvals placed in
    # one execute cycle would each pass against the same balance and breach the
    # buffer.
    reserved = sum(q.notional for q in store.with_status(Status.EXECUTED))
    if market.cash() - reserved - p.notional < cfg.order.min_cash_buffer_usd:
        return _void(store, p, "insufficient cash above buffer")

    # Claim the proposal atomically so two scheduler threads can't both submit.
    if not store.transition(p, Status.APPROVED, Status.EXECUTED):
        return False
    coid = f"smartcap-{p.id}"  # deterministic: broker dedups a retry, never double-fills
    try:
        resp = market.trading.submit_order(LimitOrderRequest(
            symbol=p.symbol, qty=p.qty, side=OrderSide.BUY,
            limit_price=round(p.limit_high, 2),  # never worse than the approved band top
            time_in_force=TimeInForce.DAY, client_order_id=coid))
    except Exception:
        # Submit failed: roll back to APPROVED so the next cycle retries. The
        # client_order_id is only recorded on success, so the guard above never
        # strands the order.
        store.transition(p, Status.EXECUTED, Status.APPROVED)
        log.exception("order submit failed for %s (%s); will retry", p.symbol, p.id)
        store.log("order_submit_failed", p.id, symbol=p.symbol)
        return False
    store.mark_submitted(p, coid, str(resp.id))
    store.log("order_submitted", p.id, broker_order_id=str(resp.id),
              limit_price=round(p.limit_high, 2), qty=p.qty)
    return True


def _void(store: Store, p: Proposal, reason: str) -> bool:
    # Atomic: only void a still-APPROVED proposal so we never clobber a status
    # another thread just moved on.
    if store.transition(p, Status.APPROVED, Status.VOIDED):
        p.status_reason = reason
        store.log("proposal_voided", p.id, reason=reason)
    return False


_BROKER_TERMINAL = {"filled": Status.FILLED, "canceled": Status.CANCELED,
                    "expired": Status.CANCELED, "rejected": Status.CANCELED}


def sync_orders(store: Store, market: Market) -> list[tuple[str, str]]:
    """Track submitted orders to their end state. Returns (symbol, outcome)
    transitions for user notification."""
    changes = []
    for p in store.with_status(Status.EXECUTED):
        try:
            order = market.trading.get_order_by_client_id(p.client_order_id)
            outcome = _BROKER_TERMINAL.get(str(order.status.value))
            if outcome and store.transition(p, Status.EXECUTED, outcome):
                p.status_reason = (f"filled {order.filled_qty} @ {order.filled_avg_price}"
                                   if outcome is Status.FILLED else str(order.status.value))
                store.log("order_" + outcome.value, p.id, detail=p.status_reason)
                changes.append((p.symbol, p.status_reason))
        except Exception:
            # One broker error must not abort tracking of the other open orders.
            log.exception("sync failed for %s (%s)", p.symbol, p.id)
    return changes
