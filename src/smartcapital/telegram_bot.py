"""Telegram approvals: one allowlisted chat, signed single-use tokens bound to
the proposal and its price band, TTL expiry (unanswered = no action).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from smartcapital import db, security
from smartcapital.config import secrets
from smartcapital.db import Proposal, Status

log = logging.getLogger(__name__)


def format_message(p: Proposal) -> str:
    v = p.llm_verdict or {}
    ta = (p.packet or {}).get("technicals", {})
    fu = (p.packet or {}).get("fundamentals", {})
    risks = "\n".join(f"  • {r}" for r in v.get("key_risks", [])) or "  • (none listed)"
    return (
        f"*BUY {p.symbol}?*  (trigger: {p.trigger_type})\n"
        f"{p.qty:g} shares ≈ ${p.notional:,.0f}\n"
        f"Limit band: ${p.limit_low:,.2f} – ${p.limit_high:,.2f}\n"
        f"Expires: {p.expires_at:%H:%M} UTC\n\n"
        f"Price ${ta.get('price')}, day {ta.get('day_change_pct')}%, "
        f"vs EMA-200 {ta.get('pct_vs_ema200')}%, off 52w high {ta.get('pct_off_52w_high')}%\n"
        f"P/E {fu.get('pe_ttm') and round(fu['pe_ttm'], 1)}, sector {fu.get('sector')}\n\n"
        f"*Why:* {v.get('reasoning', '')}\n\n"
        f"*Risks:*\n{risks}\n\n"
        f"Confidence: {v.get('confidence', '?')}  ·  Model: {p.llm_model}"
    )


class ApprovalBot:
    def __init__(self) -> None:
        s = secrets()
        self.chat_id = str(s.telegram_allowed_chat_id)
        self.app = Application.builder().token(s.telegram_bot_token).build()
        self.app.add_handler(CallbackQueryHandler(self.on_callback))

    async def send_proposal(self, proposal_id: str) -> None:
        with db.session() as s:
            p = s.get(Proposal, proposal_id)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=security.make_callback(
                    "approve", p.id, p.nonce, p.limit_low, p.limit_high)),
                InlineKeyboardButton("❌ Deny", callback_data=security.make_callback(
                    "deny", p.id, p.nonce, p.limit_low, p.limit_high)),
            ]])
            await self.app.bot.send_message(chat_id=self.chat_id, text=format_message(p),
                                            parse_mode="Markdown", reply_markup=kb)
            db.log(s, "proposal_sent", p.id)

    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        chat_id = str(q.message.chat_id) if q.message else None
        if chat_id != self.chat_id:
            log.warning("callback from non-allowlisted chat %s dropped", chat_id)
            return

        with db.session() as s:
            p, decision = self._match(s, q.data)
            if p is None:
                await q.answer("Invalid or already-used token.", show_alert=True)
                return
            now = datetime.now(timezone.utc)
            if p.status is not Status.PENDING:
                await q.answer(f"Already {p.status.value}.", show_alert=True)
                return
            if p.expires_at and now > db.as_utc(p.expires_at):
                p.status = Status.EXPIRED
                db.log(s, "proposal_expired", p.id)
                await q.answer("Expired — no action taken.", show_alert=True)
                return

            p.nonce = db.new_id()  # rotate: old token is now single-use spent
            p.decided_at = now
            if decision == "approve":
                p.status = Status.APPROVED
                db.log(s, "proposal_approved", p.id)
                await q.answer("Approved — order will be placed if price is still in band.")
            else:
                p.status = Status.DENIED
                db.log(s, "proposal_denied", p.id)
                await q.answer("Denied. No action taken.")

    def _match(self, s, data: str):
        for p in db.pending_proposals(s):
            decision = security.verify_callback(data, p.id, p.nonce, p.limit_low, p.limit_high)
            if decision:
                return p, decision
        return None, None

    async def notify(self, text: str) -> None:
        await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")


def expire_stale(now: datetime | None = None) -> int:
    """Server-side sweep so unanswered proposals die even if Telegram is down."""
    now = now or datetime.now(timezone.utc)
    n = 0
    with db.session() as s:
        for p in db.pending_proposals(s):
            if p.expires_at and now > db.as_utc(p.expires_at):
                p.status = Status.EXPIRED
                db.log(s, "proposal_expired", p.id, swept=True)
                n += 1
    return n
