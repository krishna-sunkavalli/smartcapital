"""Portfolio-review triggers (new in v2): the full sell/portfolio-review loop.

Weekly scheduled review, drawdown, concentration, earnings released for a held
name, and the thesis-break check that re-verifies conditions cited in the
original buy analysis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiis.config import TriggersCfg
from aiis.db.models import Analysis, Position
from aiis.triggers.buy_triggers import Trigger

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6}


def detect_review_triggers(
    position: Position,
    current_price: float,
    managed_capital: float,
    cfg: TriggersCfg,
    *,
    now: datetime | None = None,
    last_weekly_review: datetime | None = None,
    earnings_released: bool = False,
    origin_analysis: Analysis | None = None,
    indicator_snapshot: dict | None = None,
) -> list[Trigger]:
    now = now or datetime.now(timezone.utc)
    out: list[Trigger] = []

    # Weekly scheduled review of every open position
    if now.weekday() == _WEEKDAYS.get(cfg.weekly_review_day, 4):
        if last_weekly_review is None or (now - last_weekly_review) > timedelta(days=6):
            out.append(Trigger("weekly_review", "review", {}))

    # Position drawdown beyond threshold
    pnl_pct = current_price / position.avg_entry_price - 1
    if pnl_pct <= -cfg.drawdown_review_pct:
        out.append(Trigger("drawdown", "review", {"pnl_pct": round(pnl_pct * 100, 2)}))

    # Position gain pushing concentration beyond limits
    if managed_capital > 0:
        weight = (position.qty * current_price) / managed_capital
        if weight >= cfg.concentration_review_pct:
            out.append(Trigger("concentration", "review", {"weight_pct": round(weight * 100, 2)}))

    # Earnings report released for a held name
    if earnings_released:
        out.append(Trigger("earnings_released", "review", {}))

    # Thesis-break check: re-verify the conditions cited in the original buy analysis
    if origin_analysis is not None and origin_analysis.thesis_conditions and indicator_snapshot:
        broken = _broken_conditions(origin_analysis.thesis_conditions, indicator_snapshot, current_price)
        if broken:
            out.append(Trigger("thesis_break", "review", {"broken_conditions": broken}))
    return out


def _broken_conditions(conditions: list, snapshot: dict, price: float) -> list[str]:
    """Conditions are structured as {"metric": ..., "op": "gt"|"lt", "value": ...}
    recorded by the judge pass at buy time. A condition is broken when it no
    longer holds against fresh deterministic data."""
    broken = []
    ctx = dict(snapshot, price=price)
    for c in conditions:
        metric, op, target = c.get("metric"), c.get("op"), c.get("value")
        actual = ctx.get(metric)
        if actual is None or target is None:
            continue
        holds = actual > target if op == "gt" else actual < target if op == "lt" else True
        if not holds:
            broken.append(f"{metric} {op} {target} no longer holds (actual {round(float(actual), 4)})")
    return broken
