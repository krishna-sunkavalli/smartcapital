"""Three-pass adversarial orchestration: bear -> bull -> judge.

Every run - including Watch and Pass outcomes - is persisted with the full
data packet, all three passes, pinned model + prompt version, and a
hypothetical entry price so every decision can be scored later against the
no-LLM control baseline.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from aiis.analyst import prompts
from aiis.analyst.llm import LLM
from aiis.analyst.packet import validate_packet
from aiis.config import AppConfig
from aiis.data.base import Packet
from aiis.db.models import Analysis, Recommendation, TriggerEvent

_VALID = {
    "buy": {"buy", "watch", "pass"},
    "portfolio_review": {"hold", "trim", "sell"},
}


def run_analysis(
    session: Session,
    llm: LLM,
    cfg: AppConfig,
    trigger_event: TriggerEvent,
    packet: Packet,
    kind: str,  # "buy" | "portfolio_review"
) -> Analysis:
    # Reject before any tokens are spent if data is missing/stale/inconsistent.
    validate_packet(packet, kind, cfg)
    prompt_packet = packet.to_prompt()
    symbol = trigger_event.symbol

    bear_t = prompts.BEAR_BUY if kind == "buy" else prompts.BEAR_REVIEW
    bull_t = prompts.BULL_BUY if kind == "buy" else prompts.BULL_REVIEW
    judge_t = prompts.JUDGE_BUY if kind == "buy" else prompts.JUDGE_REVIEW

    bear = llm.complete(prompts.SYSTEM, prompts.render(
        bear_t, symbol=symbol, trigger_type=trigger_event.trigger_type, packet=prompt_packet))
    bull = llm.complete(prompts.SYSTEM, prompts.render(
        bull_t, symbol=symbol, bear_case=bear, packet=prompt_packet))
    judge = llm.judge(prompts.SYSTEM, prompts.render(
        judge_t, symbol=symbol, bear_case=bear, bull_case=bull))

    rec = str(judge.get("recommendation", "")).lower()
    if rec not in _VALID[kind]:
        # An out-of-schema verdict is treated as the conservative outcome, and
        # the raw output is preserved for prompt review.
        judge["_schema_violation"] = rec
        rec = "pass" if kind == "buy" else "hold"

    analysis = Analysis(
        trigger_event_id=trigger_event.id,
        symbol=symbol,
        kind=kind,
        model=cfg.analyst.model,
        prompt_version=prompts.PROMPT_VERSION,
        temperature=cfg.analyst.temperature,
        samples=cfg.analyst.samples,
        data_packet=prompt_packet,
        bear_case=bear,
        bull_case=bull,
        judge_output=judge,
        recommendation=Recommendation(rec),
        confidence_label=judge.get("confidence_label"),
        hypothetical_entry_price=float(packet.fields["latest_price"].value),
        thesis_conditions=judge.get("thesis_conditions") or [],
    )
    session.add(analysis)
    trigger_event.status = "analyzed"
    session.flush()
    return analysis
