"""Telegram approvals: the buy proposal goes to your chat with Approve/Deny
buttons; unanswered proposals expire after the TTL (expiry = no action).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from smartcapital.config import secrets
from smartcapital.state import Proposal, Status, Store

log = logging.getLogger(__name__)


def format_message(p: Proposal) -> str:
    """Telegram HTML approval card. All LLM/dynamic content is escaped so reasoning
    or headlines containing <, >, & can never break parsing and silently drop the
    proposal."""
    v = p.llm_verdict or {}
    ta = (p.packet or {}).get("technicals", {})
    fu = (p.packet or {}).get("fundamentals", {})
    e = html.escape

    def num(x, suffix="", signed=False):
        if x is None:
            return "—"
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return e(str(x))
        return f"{'+' if signed and xf > 0 else ''}{xf:g}{suffix}"

    conf = str(v.get("confidence", "—")).upper()
    badge = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠"}.get(conf, "⚪")
    sector = fu.get("sector") or "—"
    pe = fu.get("pe_ttm")
    pe_s = f"P/E {round(pe, 1)}" if isinstance(pe, (int, float)) else "P/E —"
    expires = f"{p.expires_at:%H:%M} UTC" if p.expires_at else "—"
    risks = [r for r in (v.get("key_risks") or []) if str(r).strip()][:3]
    risk_lines = "\n".join(f"• {e(str(r))}" for r in risks) or "• none listed"
    thesis = e(str(v.get("reasoning", ""))) or "—"

    return (
        f"{badge} <b>BUY · {e(p.symbol)}</b> · <i>{e(conf)}</i>\n"
        f"<i>{e(str(sector))}</i>\n\n"
        f"📉 <b>Signal</b> — {e(p.trigger_type)}, {num(ta.get('day_change_pct'), '%', signed=True)} today\n"
        f"Px ${num(ta.get('price'))} · EMA200 {num(ta.get('pct_vs_ema200'), '%', signed=True)} · "
        f"52w {num(ta.get('pct_off_52w_high'), '%', signed=True)} · {pe_s}\n\n"
        f"🧾 <b>Order</b> — {p.qty:g} sh ≈ ${p.notional:,.0f}\n"
        f"Limit ${p.limit_low:,.2f}–${p.limit_high:,.2f} · expires {expires}\n\n"
        f"🧠 <b>Thesis</b>\n<blockquote>{thesis}</blockquote>\n"
        f"⚠️ <b>Risks</b>\n{risk_lines}"
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
        try:
            await self.app.bot.send_message(chat_id=self.chat_id, text=format_message(p),
                                            parse_mode="HTML", reply_markup=kb)
        except Exception:
            # A dropped send means a silently un-actioned proposal. Surface it and
            # expire it so it never lingers as a phantom PENDING.
            log.exception("failed to send proposal %s for %s", p.id, p.symbol)
            self.store.log("proposal_send_failed", p.id, symbol=p.symbol)
            self.store.transition(p, Status.PENDING, Status.EXPIRED)
            return
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
            if self.store.transition(p, Status.PENDING, Status.EXPIRED):
                self.store.log("proposal_expired", p.id)
            await q.answer("Expired — no action taken.", show_alert=True)
            return

        target = Status.APPROVED if decision == "approve" else Status.DENIED
        if not self.store.transition(p, Status.PENDING, target):
            await q.answer(f"Already {p.status.value}.", show_alert=True)
            return
        p.decided_at = now
        if target is Status.APPROVED:
            self.store.log("proposal_approved", p.id)
            await q.answer("Approved — order will be placed if price is still in band.")
        else:
            self.store.log("proposal_denied", p.id)
            await q.answer("Denied. No action taken.")

    async def notify(self, text: str) -> None:
        # Fire-and-forget from the scheduler: swallow and log transport errors so
        # a Telegram hiccup never crashes the caller or vanishes silently.
        try:
            await self.app.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception:
            log.exception("failed to send notification")


def expire_stale(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    n = 0
    for p in store.with_status(Status.PENDING):
        if p.expires_at and now > p.expires_at:
            # Atomic: a proposal the user approved microseconds ago must not be
            # clobbered to EXPIRED by this sweep.
            if store.transition(p, Status.PENDING, Status.EXPIRED):
                store.log("proposal_expired", p.id, swept=True)
                n += 1
    return n
