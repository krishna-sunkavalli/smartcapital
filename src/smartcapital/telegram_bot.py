"""Telegram approvals: the buy proposal goes to your chat with Approve/Deny
buttons; unanswered proposals expire after the TTL (expiry = no action).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from smartcapital.config import secrets
from smartcapital.state import Proposal, Status, Store

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
    def __init__(self, store: Store) -> None:
        s = secrets()
        self.store = store
        self.chat_id = str(s.telegram_chat_id)
        self.app = Application.builder().token(s.telegram_bot_token).build()
        self.app.add_handler(CallbackQueryHandler(self.on_callback))

    async def send_proposal(self, proposal_id: str) -> None:
        p = self.store.get(proposal_id)
        if p is None:
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{p.id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{p.id}"),
        ]])
        await self.app.bot.send_message(chat_id=self.chat_id, text=format_message(p),
                                        parse_mode="Markdown", reply_markup=kb)
        self.store.log("proposal_sent", p.id)

    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        try:
            decision, proposal_id = q.data.split(":", 1)
        except ValueError:
            await q.answer("Malformed callback.", show_alert=True)
            return
        p = self.store.get(proposal_id)
        if p is None:
            await q.answer("Unknown proposal.", show_alert=True)
            return
        now = datetime.now(timezone.utc)
        if p.status is not Status.PENDING:
            await q.answer(f"Already {p.status.value}.", show_alert=True)
            return
        if p.expires_at and now > p.expires_at:
            p.status = Status.EXPIRED
            self.store.log("proposal_expired", p.id)
            await q.answer("Expired — no action taken.", show_alert=True)
            return

        p.decided_at = now
        if decision == "approve":
            p.status = Status.APPROVED
            self.store.log("proposal_approved", p.id)
            await q.answer("Approved — order will be placed if price is still in band.")
        else:
            p.status = Status.DENIED
            self.store.log("proposal_denied", p.id)
            await q.answer("Denied. No action taken.")

    async def notify(self, text: str) -> None:
        await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")


def expire_stale(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    n = 0
    for p in store.with_status(Status.PENDING):
        if p.expires_at and now > p.expires_at:
            p.status = Status.EXPIRED
            store.log("proposal_expired", p.id, swept=True)
            n += 1
    return n
