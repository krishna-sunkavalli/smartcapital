"""Deterministic guardrails. The LLM cannot override any of these. They run
twice: at proposal time, and again immediately before execution against
refreshed data. Any violation blocks the action; there is no override path in
code.

Pure functions over an explicit context snapshot, so every check is unit
testable and the pre-execution re-check is literally the same code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aiis.config import AppConfig


@dataclass
class Violation:
    rule: str
    detail: str


@dataclass
class ProposedOrder:
    symbol: str
    action: str            # buy | trim | sell
    qty: float
    notional: float        # qty * reference price
    reference_price: float
    limit_low: float
    limit_high: float
    sector: str = "unknown"


@dataclass
class GuardrailContext:
    """Snapshot of the world the checks run against. Built fresh both at
    proposal time and immediately before submission."""

    now: datetime
    account_equity: float
    available_cash: float
    sp500_symbols: set[str]
    positions: dict[str, dict]              # symbol -> {qty, price, sector, notional}
    deployed_today: float
    deployed_this_week: float
    proposals_last_hour: int
    proposals_last_day: int
    latest_price: float | None = None       # refreshed price at check time
    market_is_open: bool = True
    minutes_since_open: float | None = None
    minutes_to_close: float | None = None
    halted_symbols: set[str] = field(default_factory=set)
    market_wide_circuit_breaker: bool = False
    kill_switch_active: bool = False
    next_earnings_date: datetime | None = None


CORRELATED_TECH_SECTORS = {"Technology", "Information Technology", "Communication Services",
                           "Consumer Cyclical", "Consumer Discretionary"}


def check_all(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    v: list[Violation] = []
    v += _kill_and_anomaly(ctx, cfg)
    v += _universe(order, ctx, cfg)
    v += _market_state(order, ctx, cfg)
    if order.action == "buy":
        v += _exposure(order, ctx, cfg)
        v += _cash(order, ctx, cfg)
    v += _price_tolerance(order, ctx)
    v += _phase_limits(order, ctx, cfg)
    return v


def _kill_and_anomaly(ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    v = []
    if ctx.kill_switch_active:
        v.append(Violation("kill_switch", "kill switch is active; all proposals disabled"))
    if ctx.market_wide_circuit_breaker:
        v.append(Violation("circuit_breaker", "market-wide circuit breaker tripped; automatic halt"))
    if ctx.proposals_last_hour >= cfg.safety.max_proposals_per_rolling_hour:
        v.append(Violation("anomaly_halt",
                           f"{ctx.proposals_last_hour} proposals in the last hour >= "
                           f"{cfg.safety.max_proposals_per_rolling_hour}"))
    if ctx.proposals_last_day >= cfg.safety.max_proposals_per_rolling_day:
        v.append(Violation("anomaly_halt",
                           f"{ctx.proposals_last_day} proposals in the last day >= "
                           f"{cfg.safety.max_proposals_per_rolling_day}"))
    return v


def _universe(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    v = []
    if cfg.universe.sp500_only and order.symbol not in ctx.sp500_symbols:
        v.append(Violation("universe", f"{order.symbol} is not in the S&P 500"))
    if order.action not in ("buy", "trim", "sell"):
        v.append(Violation("instrument", f"action '{order.action}' is not allowed (long equity only)"))
    if order.qty <= 0:
        v.append(Violation("instrument", "quantity must be positive (no shorting)"))
    if order.action in ("trim", "sell"):
        held = ctx.positions.get(order.symbol, {}).get("qty", 0.0)
        if order.qty > held + 1e-9:
            v.append(Violation("instrument",
                               f"sell qty {order.qty} exceeds held {held} (no shorting)"))
    return v


def _market_state(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    v = []
    if not ctx.market_is_open:
        v.append(Violation("market_closed", "no orders while the market is closed"))
    buf = cfg.orders.auction_buffer_minutes
    if ctx.minutes_since_open is not None and ctx.minutes_since_open < buf:
        v.append(Violation("auction_window", f"within {buf}min of the opening auction"))
    if ctx.minutes_to_close is not None and ctx.minutes_to_close < buf:
        v.append(Violation("auction_window", f"within {buf}min of the closing auction"))
    if order.symbol in ctx.halted_symbols:
        v.append(Violation("halt", f"{order.symbol} is halted"))
    return v


def _exposure(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    v = []
    e = cfg.exposure
    managed_cap = ctx.account_equity * e.managed_capital_pct
    current_managed = sum(p["notional"] for p in ctx.positions.values())

    # Hard ceiling on total system-managed capital, separate from flow limits
    if current_managed + order.notional > managed_cap:
        v.append(Violation("managed_capital",
                           f"total managed {current_managed + order.notional:.0f} would exceed "
                           f"ceiling {managed_cap:.0f} ({e.managed_capital_pct:.0%} of equity)"))

    # Maximum position size per name
    existing = ctx.positions.get(order.symbol, {}).get("notional", 0.0)
    if existing + order.notional > managed_cap * e.max_position_pct:
        v.append(Violation("position_size",
                           f"{order.symbol} position would be {existing + order.notional:.0f}, "
                           f"limit {managed_cap * e.max_position_pct:.0f}"))

    # Maximum sector exposure - ten in-limit positions can still be one
    # correlated Nasdaq bet, so sector and correlated-cluster caps both apply.
    sector_notional = sum(
        p["notional"] for p in ctx.positions.values() if p.get("sector") == order.sector
    ) + order.notional
    if sector_notional > managed_cap * e.max_sector_pct:
        v.append(Violation("sector_exposure",
                           f"sector '{order.sector}' would be {sector_notional:.0f}, "
                           f"limit {managed_cap * e.max_sector_pct:.0f}"))
    cluster = sum(
        p["notional"] for p in ctx.positions.values() if p.get("sector") in CORRELATED_TECH_SECTORS
    )
    if order.sector in CORRELATED_TECH_SECTORS:
        cluster += order.notional
    if cluster > managed_cap * e.max_correlated_tech_pct:
        v.append(Violation("correlated_cluster",
                           f"tech/growth cluster would be {cluster:.0f}, "
                           f"limit {managed_cap * e.max_correlated_tech_pct:.0f}"))

    # Daily and weekly deployment flow limits
    if ctx.deployed_today + order.notional > e.max_daily_deployment:
        v.append(Violation("daily_deployment",
                           f"daily deployment would reach {ctx.deployed_today + order.notional:.0f}, "
                           f"limit {e.max_daily_deployment:.0f}"))
    if ctx.deployed_this_week + order.notional > e.max_weekly_deployment:
        v.append(Violation("weekly_deployment",
                           f"weekly deployment would reach {ctx.deployed_this_week + order.notional:.0f}, "
                           f"limit {e.max_weekly_deployment:.0f}"))
    return v


def _cash(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    if ctx.available_cash - order.notional < cfg.exposure.min_cash_buffer:
        return [Violation("available_cash",
                          f"cash {ctx.available_cash:.0f} minus order {order.notional:.0f} would "
                          f"breach buffer {cfg.exposure.min_cash_buffer:.0f}")]
    return []


def _price_tolerance(order: ProposedOrder, ctx: GuardrailContext) -> list[Violation]:
    """If the refreshed price has left the approved band, the proposal is
    voided upstream - never resubmitted with a new price."""
    if ctx.latest_price is None:
        return [Violation("price_check", "no refreshed price available at check time")]
    if not (order.limit_low <= ctx.latest_price <= order.limit_high):
        return [Violation("price_band",
                          f"price {ctx.latest_price} is outside approved band "
                          f"[{order.limit_low}, {order.limit_high}]; proposal must be voided")]
    return []


def _phase_limits(order: ProposedOrder, ctx: GuardrailContext, cfg: AppConfig) -> list[Violation]:
    """Rollout training wheels. Phase 2 caps order notional regardless of the
    configured exposure limits."""
    if cfg.rollout.phase == 2 and order.notional > cfg.rollout.phase2_max_order_notional:
        return [Violation("phase2_limit",
                          f"order notional {order.notional:.0f} exceeds phase-2 cap "
                          f"{cfg.rollout.phase2_max_order_notional:.0f}")]
    return []
