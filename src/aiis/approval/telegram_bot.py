"""Hardened Telegram approval bot.

Security posture:
- Restricted to a single allowlisted chat id; anything else is dropped + logged.
- Callback payloads HMAC-signed; approvals are single-use nonces (Approval row).
- Approval bound to the specific proposal AND its price band.
- Time-limited: expiry or rejection causes no action; the expiry sweep runs
  server-side, so a dead phone can never leave a live actionable proposal.
- Buttons are inert until min_read_delay elapses; large orders require typing
  the ticker back, not just tapping a button.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aiis.approval import security
from aiis.approval.fatigue import buttons_active, needs_typeback
from aiis.config import AppConfig, secrets
from aiis.db.models import Analysis, Approval, Proposal, ProposalStatus
from aiis.db.session import get_session
from aiis.execution.audit import append_audit

log = logging.getLogger(__name__)


def format_proposal_message(p: Proposal, a: Analysis) -> str:
    """Everything the spec requires in the approval message: ticker, action,
    size, band, originating trigger, assessments with data timestamps, bear
    case, bull case, risks, recommendation."""
    packet = a.data_packet or {}

    def stamp(name: str) -> str:
        f = packet.get(name) or {}
        return f"{f.get('source', '?')} @ {str(f.get('as_of', '?'))[:16]}"

    judge = a.judge_output or {}
    risks = "\n".join(f"  - {r}" for r in judge.get("key_risks", [])) or "  - (none listed)"
    return (
        f"*{p.action.upper()} {p.symbol}*  ({a.kind}, trigger: `{a.trigger_event_id[:8]}`)\n"
        f"Size: {p.qty:g} sh  (~${p.notional:,.0f})\n"
        f"Limit band: ${p.limit_low:,.2f} - ${p.limit_high:,.2f} (ref ${p.reference_price:,.2f})\n"
        f"Expires: {p.expires_at:%H:%M UTC}\n\n"
        f"*Technical* ({stamp('indicators')}):\n`{_short(packet.get('indicators', {}).get('value'))}`\n"
        f"*Fundamentals* ({stamp('ratios')}):\n`{_short(packet.get('ratios', {}).get('value'))}`\n\n"
        f"*Bear case:*\n{a.bear_case[:700]}\n\n"
        f"*Bull case:*\n{a.bull_case[:700]}\n\n"
        f"*Principal risks:*\n{risks}\n\n"
        f"*Recommendation:* {a.recommendation.value.upper()} "
        f"(confidence label: {a.confidence_label or 'n/a'})\n"
        f"Model {a.model} / prompt v{a.prompt_version}"
    )


def _short(d, limit: int = 300) -> str:
    return str(d)[:limit]


class ApprovalBot:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        s = secrets()
        self.allowed_chat_id = str(s.telegram_allowed_chat_id)
        self.app = Application.builder().token(s.telegram_bot_token).build()
        self.app.add_handler(CallbackQueryHandler(self.on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

    # --- outbound ----------------------------------------------------------
    async def send_proposal(self, proposal_id: str) -> None:
        with get_session() as session:
            p = session.get(Proposal, proposal_id)
            a = session.get(Analysis, p.analysis_id)
            approval = session.get(Approval, p.approval.id) if p.approval else None
            if approval is None:
                approval = Approval(proposal_id=p.id, nonce=security.new_nonce(),
                                    typeback_required=int(needs_typeback(p.notional, self.cfg.approval)))
                session.add(approval)
                session.flush()
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=security.make_callback(
                    "approve", p.id, approval.nonce, p.limit_low, p.limit_high)),
                InlineKeyboardButton("❌ Reject", callback_data=security.make_callback(
                    "reject", p.id, approval.nonce, p.limit_low, p.limit_high)),
            ]])
            text = format_proposal_message(p, a)
            if approval.typeback_required:
                text += (f"\n\n⚠️ Order above ${self.cfg.approval.typeback_notional_threshold:,.0f}: "
                         f"after tapping Approve, type the ticker `{p.symbol}` to confirm.")
            text += (f"\n\n⏳ Buttons activate in {self.cfg.approval.min_read_delay_seconds}s - "
                     f"read the bear case first.")
            await self.app.bot.send_message(chat_id=self.allowed_chat_id, text=text,
                                            parse_mode="Markdown", reply_markup=kb)
            approval.message_sent_at = datetime.now(timezone.utc)
            approval.buttons_active_at = approval.message_sent_at + timedelta(
                seconds=self.cfg.approval.min_read_delay_seconds)
            append_audit(session, "proposal_sent", p.id, {"chat_id": self.allowed_chat_id})

    # --- inbound -----------------------------------------------------------
    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        chat_id = str(q.message.chat_id) if q.message else None
        if chat_id != self.allowed_chat_id:
            log.warning("callback from non-allowlisted chat %s dropped", chat_id)
            with get_session() as session:
                append_audit(session, "unauthorized_callback", None, {"chat_id": chat_id})
            return

        with get_session() as session:
            approval, proposal, decision = self._verify(session, q.data)
            if approval is None:
                await q.answer("Invalid or already-used approval token.", show_alert=True)
                return
            now = datetime.now(timezone.utc)
            if proposal.status is not ProposalStatus.PENDING or now > proposal.expires_at:
                self._expire(session, proposal, approval)
                await q.answer("Proposal expired - no action was taken.", show_alert=True)
                return
            if not buttons_active(approval, self.cfg.approval, now):
                remaining = int((approval.buttons_active_at - now).total_seconds())
                await q.answer(f"Buttons not active yet - {remaining}s left. Read the bear case.",
                               show_alert=True)
                return

            if decision == "reject":
                approval.consumed = 1
                approval.decision = "rejected"
                approval.decided_at = now
                approval.decided_by_chat_id = chat_id
                proposal.status = ProposalStatus.REJECTED
                append_audit(session, "proposal_rejected", proposal.id, {})
                await q.answer("Rejected. No action taken.")
                return

            # decision == "approve"
            if approval.typeback_required and not approval.typeback_ok:
                approval.decided_by_chat_id = chat_id  # arm typeback; nonce not yet consumed
                append_audit(session, "typeback_requested", proposal.id, {})
                await q.answer(f"Type the ticker ({proposal.symbol}) in chat to finalize.",
                               show_alert=True)
                return
            self._finalize_approval(session, approval, proposal, chat_id, now)
            await q.answer("Approved - submitting within guardrails.")

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.message.chat_id)
        if chat_id != self.allowed_chat_id:
            return
        text = (update.message.text or "").strip().upper()
        with get_session() as session:
            # A pending typeback is an armed, unconsumed approval on a live proposal.
            pending = [
                ap for ap in session.query(Approval).filter(
                    Approval.typeback_required == 1, Approval.typeback_ok == 0,
                    Approval.consumed == 0, Approval.decided_by_chat_id == chat_id)
                if ap.proposal.status is ProposalStatus.PENDING
            ]
            for ap in pending:
                if text == ap.proposal.symbol.upper():
                    now = datetime.now(timezone.utc)
                    if now > ap.proposal.expires_at:
                        self._expire(session, ap.proposal, ap)
                        await update.message.reply_text("Proposal expired - no action taken.")
                        return
                    ap.typeback_ok = 1
                    self._finalize_approval(session, ap, ap.proposal, chat_id, now)
                    await update.message.reply_text(
                        f"Ticker confirmed - {ap.proposal.symbol} approved.")
                    return

    # --- helpers -----------------------------------------------------------
    def _verify(self, session, data: str):
        for approval in session.query(Approval).filter(Approval.consumed == 0):
            p = approval.proposal
            decision = security.verify_callback(data, p.id, approval.nonce, p.limit_low, p.limit_high)
            if decision:
                return approval, p, decision
        return None, None, None

    def _finalize_approval(self, session, approval: Approval, proposal: Proposal,
                           chat_id: str, now: datetime) -> None:
        approval.consumed = 1  # single-use
        approval.decision = "approved"
        approval.decided_at = now
        approval.decided_by_chat_id = chat_id
        proposal.status = ProposalStatus.APPROVED
        append_audit(session, "proposal_approved", proposal.id,
                     {"band": [proposal.limit_low, proposal.limit_high], "chat_id": chat_id})

    def _expire(self, session, proposal: Proposal, approval: Approval) -> None:
        if proposal.status is ProposalStatus.PENDING:
            proposal.status = ProposalStatus.EXPIRED
            approval.decision = "expired"
            approval.consumed = 1
            append_audit(session, "proposal_expired", proposal.id, {})


def expire_stale_proposals(now: datetime | None = None) -> int:
    """Server-side expiry sweep; runs on the scheduler regardless of Telegram."""
    now = now or datetime.now(timezone.utc)
    n = 0
    with get_session() as session:
        for p in session.query(Proposal).filter(Proposal.status == ProposalStatus.PENDING,
                                                Proposal.expires_at < now):
            p.status = ProposalStatus.EXPIRED
            if p.approval:
                p.approval.decision = "expired"
                p.approval.consumed = 1
            append_audit(session, "proposal_expired", p.id, {"swept": True})
            n += 1
    return n
