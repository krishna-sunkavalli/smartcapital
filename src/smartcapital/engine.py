"""The pipeline, exactly the v1 flow:

trigger -> gather TA + fundamentals -> LLM buy/decline -> if buy, Telegram
approval -> if approved, limit order.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from smartcapital import analyst, fundamentals, triggers
from smartcapital.config import Config
from smartcapital.market import Market
from smartcapital.state import Proposal, Status, Store, utcnow

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.market = Market()

    def scan(self) -> list[str]:
        """One polling cycle. Returns ids of proposals awaiting approval."""
        if not self.market.market_open():
            return []
        created: list[str] = []
        for symbol in self.cfg.watchlist:
            try:
                created += self._scan_symbol(symbol)
            except Exception:
                log.exception("scan failed for %s", symbol)
        return created

    def _scan_symbol(self, symbol: str) -> list[str]:
        df = self.market.daily_bars(symbol)
        if df is None or df.empty:
            return []
        price = self.market.latest_price(symbol)
        fired = [t for t in triggers.detect(df, price, self.cfg.triggers)
                 if not self.store.in_cooldown(symbol, t.trigger_type)]
        if not fired:
            return []

        out = []
        for trig in fired:
            self.store.start_cooldown(symbol, trig.trigger_type,
                                      utcnow() + timedelta(days=self.cfg.triggers.cooldown_days))
            self.store.log("trigger_fired", None, symbol=symbol,
                           trigger=trig.trigger_type, **trig.details)

            packet = {
                "technicals": triggers.ta_snapshot(df, price),
                "fundamentals": fundamentals.snapshot(symbol),
            }
            verdict = analyst.analyze(symbol, trig.trigger_type, trig.details,
                                      packet, self.cfg.llm)

            band = self.cfg.order.price_band_pct
            qty = max(1, int(self.cfg.order.notional_usd // price))
            is_buy = verdict["recommendation"] == "buy"
            p = self.store.add(Proposal(
                symbol=symbol,
                trigger_type=trig.trigger_type,
                trigger_details=trig.details,
                packet=packet,
                llm_model=verdict.pop("model", self.cfg.llm.model),
                llm_verdict=verdict,
                reference_price=price,
                limit_low=round(price * (1 - band), 2),
                limit_high=round(price * (1 + band), 2),
                qty=float(qty),
                notional=qty * price,
                status=Status.PENDING if is_buy else Status.DECLINED,
                expires_at=(utcnow() + timedelta(minutes=self.cfg.approval.ttl_minutes)
                            if is_buy else None),
            ))
            self.store.log("llm_" + verdict["recommendation"], p.id, symbol=symbol)
            if p.status is Status.PENDING:
                out.append(p.id)
        return out
