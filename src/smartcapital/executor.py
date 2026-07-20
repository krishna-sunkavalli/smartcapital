"""Order placement. Before submitting an approved proposal we re-check that
the market is open, cash is sufficient, and the live price is still inside the
approved band (outside = VOID, never resubmit). Limit orders only; the client
order id is derived from the proposal id so a retry can't double-submit.
"""
from __future__ import annotations

import logging

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from smartcapital.config import Config
from smartcapital.market import Market
from smartcapital.state import Proposal, Status, Store

log = logging.getLogger(__name__)


def execute(store: Store, p: Proposal, market: Market, cfg: Config) -> bool:
    if p.status is not Status.APPROVED:
        return False
    if p.client_order_id:  # already submitted: never resubmit
        return False

    if not market.market_open():
        return False  # not voided - retried next cycle while approval is fresh
    live = market.latest_price(p.symbol)
    if not (p.limit_low <= live <= p.limit_high):
        return _void(store, p, f"price {live} left approved band [{p.limit_low}, {p.limit_high}]")
    if market.cash() - p.notional < cfg.order.min_cash_buffer_usd:
        return _void(store, p, "insufficient cash above buffer")

    p.client_order_id = f"smartcap-{p.id}"
    resp = market.trading.submit_order(LimitOrderRequest(
        symbol=p.symbol, qty=p.qty, side=OrderSide.BUY,
        limit_price=round(p.limit_high, 2),  # never worse than the approved band top
        time_in_force=TimeInForce.DAY, client_order_id=p.client_order_id))
    p.broker_order_id = str(resp.id)
    p.status = Status.EXECUTED
    store.log("order_submitted", p.id, broker_order_id=str(resp.id),
              limit_price=round(p.limit_high, 2), qty=p.qty)
    return True


def _void(store: Store, p: Proposal, reason: str) -> bool:
    p.status = Status.VOIDED
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
        order = market.trading.get_order_by_client_id(p.client_order_id)
        outcome = _BROKER_TERMINAL.get(str(order.status.value))
        if outcome:
            p.status = outcome
            p.status_reason = (f"filled {order.filled_qty} @ {order.filled_avg_price}"
                               if outcome is Status.FILLED else str(order.status.value))
            store.log("order_" + outcome.value, p.id, detail=p.status_reason)
            changes.append((p.symbol, p.status_reason))
    return changes
