"""Scores every logged recommendation - including Pass and Watch - against
hypothetical outcomes at horizon, and compares the LLM layer against the
no-LLM control baseline. This is the honest evaluation: the LLM's judgment
cannot be backtested (it knows historical outcomes), so forward paper scoring
is the only real test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiis.db.models import Analysis, AuditEvent, Recommendation


def score_analyses(session: Session, price_lookup, horizon_days: int = 21,
                   now: datetime | None = None) -> list[dict]:
    """price_lookup(symbol) -> current price. Each analysis older than the
    horizon gets the hypothetical return it would have produced.

    Scoring convention:
      buy/hold  -> credited with the forward return (they said 'own it')
      pass/sell -> credited with the AVOIDED return (negative return = good call)
      watch/trim -> scored as half-exposure decisions
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=horizon_days)
    results = []
    for a in session.execute(select(Analysis).where(Analysis.created_at <= cutoff)).scalars():
        current = price_lookup(a.symbol)
        if not current:
            continue
        fwd = current / a.hypothetical_entry_price - 1
        rec = a.recommendation
        if rec in (Recommendation.BUY, Recommendation.HOLD):
            score = fwd
        elif rec in (Recommendation.PASS, Recommendation.SELL):
            score = -fwd
        else:  # watch, trim: half exposure
            score = fwd * 0.5 if rec is Recommendation.WATCH else -fwd * 0.5
        results.append({
            "analysis_id": a.id, "symbol": a.symbol, "kind": a.kind,
            "recommendation": rec.value, "entry": a.hypothetical_entry_price,
            "price_at_horizon": current, "fwd_return_pct": round(fwd * 100, 2),
            "decision_score_pct": round(score * 100, 2),
            "model": a.model, "prompt_version": a.prompt_version,
        })
    return results


def control_baseline(session: Session, price_lookup, horizon_days: int = 21,
                     now: datetime | None = None) -> list[dict]:
    """Score the 'trigger fired, alert sent, no LLM' control arm: assume the
    naive policy of acting on every trigger at the logged price. The LLM layer
    must demonstrably beat this before it earns its cost and nondeterminism."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=horizon_days)
    out = []
    for ev in session.execute(
        select(AuditEvent).where(AuditEvent.kind == "control_baseline_alert",
                                 AuditEvent.at <= cutoff)
    ).scalars():
        symbol, price = ev.payload.get("symbol"), ev.payload.get("price")
        current = price_lookup(symbol) if symbol else None
        if not (price and current):
            continue
        out.append({"symbol": symbol, "trigger": ev.payload.get("trigger"),
                    "fwd_return_pct": round((current / price - 1) * 100, 2)})
    return out


def summarize(llm_scores: list[dict], control_scores: list[dict]) -> dict:
    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0
    return {
        "llm_n": len(llm_scores),
        "llm_mean_decision_score_pct": round(mean([s["decision_score_pct"] for s in llm_scores]), 3),
        "control_n": len(control_scores),
        "control_mean_return_pct": round(mean([s["fwd_return_pct"] for s in control_scores]), 3),
        "recommendation_mix": _mix(llm_scores),
    }


def _mix(scores: list[dict]) -> dict:
    mix: dict[str, int] = {}
    for s in scores:
        mix[s["recommendation"]] = mix.get(s["recommendation"], 0) + 1
    return mix
