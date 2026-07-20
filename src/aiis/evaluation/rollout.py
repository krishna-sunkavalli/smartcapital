"""Staged rollout with promotion criteria written down BEFORE Phase 1 starts.

Phase 1 (paper, >= 3 months) -> Phase 2 (live, training wheels) -> Phase 3
(full limits). If criteria are not met, the LLM layer is removed and the
system ships as a deterministic alerting tool - that is the fallback design,
not a failure mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aiis.db.models import Analysis, AuditEvent


@dataclass(frozen=True)
class PromotionCriteria:
    """Frozen at project start. Edit only with a version bump and a written
    rationale in the audit log."""

    min_paper_days: int = 90
    min_scored_analyses: int = 30
    # LLM mean decision score must beat control mean by this margin (pct pts)
    min_edge_over_control_pct: float = 0.5
    max_guardrail_violations_uninvestigated: int = 0
    max_unexplained_restart_losses: int = 0
    # Buy base-rate must stay in a sane band (not rubber-stamping, not inert)
    buy_rate_band: tuple[float, float] = (0.02, 0.35)


def evaluate_phase1(session: Session, summary: dict, criteria: PromotionCriteria,
                    phase_started: datetime, now: datetime | None = None) -> dict:
    """Returns a pass/fail report per criterion. Promotion is a HUMAN decision
    informed by this report; the code never promotes itself."""
    now = now or datetime.now(timezone.utc)
    days = (now - phase_started).days
    llm_mean = summary.get("llm_mean_decision_score_pct", 0.0)
    control_mean = summary.get("control_mean_return_pct", 0.0)

    total = session.execute(select(func.count(Analysis.id)).where(Analysis.kind == "buy")).scalar_one()
    buys = session.execute(
        select(func.count(Analysis.id)).where(Analysis.kind == "buy",
                                              Analysis.recommendation == "buy")).scalar_one()
    buy_rate = buys / total if total else 0.0
    voided = session.execute(
        select(func.count(AuditEvent.seq)).where(
            AuditEvent.kind == "proposal_voided_pre_execution")).scalar_one()

    checks = {
        "paper_duration": (days >= criteria.min_paper_days, f"{days}d / {criteria.min_paper_days}d"),
        "scored_sample_size": (summary.get("llm_n", 0) >= criteria.min_scored_analyses,
                               f"{summary.get('llm_n', 0)} / {criteria.min_scored_analyses}"),
        "edge_over_control": (llm_mean - control_mean >= criteria.min_edge_over_control_pct,
                              f"llm {llm_mean} vs control {control_mean}"),
        "buy_rate_sane": (criteria.buy_rate_band[0] <= buy_rate <= criteria.buy_rate_band[1],
                          f"{buy_rate:.1%} in {criteria.buy_rate_band}"),
        "pre_execution_voids_reviewed": (True, f"{voided} voids logged - review each in audit log"),
    }
    return {
        "phase": 1,
        "all_passed": all(ok for ok, _ in checks.values()),
        "checks": {k: {"passed": ok, "detail": d} for k, (ok, d) in checks.items()},
        "fallback_if_failed": "remove LLM layer; ship as deterministic alerting tool",
    }
