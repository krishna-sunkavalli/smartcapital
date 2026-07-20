"""Base-rate monitor: rolling Buy/Watch/Pass distribution. If Buy exceeds a
threshold share of recent analyses, the system flags itself for prompt review
(the flag blocks nothing by itself, but is surfaced in the weekly digest and
the audit log).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from aiis.config import AnalystCfg
from aiis.db.models import Analysis, Recommendation, SystemFlag, utcnow


def check_buy_base_rate(session: Session, cfg: AnalystCfg) -> dict:
    rows = session.execute(
        select(Analysis.recommendation)
        .where(Analysis.kind == "buy")
        .order_by(Analysis.created_at.desc())
        .limit(cfg.buy_rate_window)
    ).scalars().all()
    total = len(rows)
    buys = sum(1 for r in rows if r == Recommendation.BUY)
    rate = buys / total if total else 0.0
    flagged = total >= 10 and rate > cfg.buy_rate_alert_threshold

    if flagged:
        flag = session.get(SystemFlag, "buy_base_rate_alert") or SystemFlag(
            name="buy_base_rate_alert", value="")
        flag.value = f"{rate:.2f}"
        flag.set_at = utcnow()
        flag.reason = (
            f"Buy share {rate:.0%} over last {total} analyses exceeds "
            f"{cfg.buy_rate_alert_threshold:.0%}; prompts need review"
        )
        session.merge(flag)
    return {"window": total, "buy_rate": rate, "flagged": flagged}
