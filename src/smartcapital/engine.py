"""The pipeline, exactly the v1 flow, at S&P-500 scale:

batch-fetch bars + prices for the whole universe -> detect triggers ->
rank by severity and apply per-cycle/daily caps -> gather TA + fundamentals
-> LLM buy/decline -> if buy, Telegram approval -> if approved, limit order.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from smartcapital import analyst, fundamentals, triggers
from smartcapital.config import Config
from smartcapital.market import Market
from smartcapital.state import Proposal, Status, Store, utcnow
from smartcapital.triggers import Trigger

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.market = Market()

    def universe(self) -> list[str]:
        if isinstance(self.cfg.watchlist, list):
            return self.cfg.watchlist
        if self.cfg.watchlist == "sp500":
            return fundamentals.sp500_symbols(self.cfg.scan.universe_cache_days)
        return [self.cfg.watchlist]

    def scan(self) -> list[str]:
        """One polling cycle. Returns ids of proposals awaiting approval."""
        if not self.market.market_open():
            return []
        symbols = self.universe()
        bars = self.market.daily_bars_multi(symbols)
        prices = self.market.latest_prices(symbols)

        # Phase 1: cheap, deterministic - detect everything that fired.
        candidates: list[tuple[str, Trigger]] = []
        for sym in symbols:
            df, price = bars.get(sym), prices.get(sym)
            if df is None or price is None:
                continue
            for trig in triggers.detect(df, price, self.cfg.triggers):
                if not self.store.in_cooldown(sym, trig.trigger_type):
                    candidates.append((sym, trig))

        # Phase 2: rank by severity and cap - the human gate is the scarce
        # resource. Skipped candidates get NO cooldown, so a still-valid
        # trigger re-competes next cycle.
        candidates.sort(key=lambda c: c[1].severity, reverse=True)
        budget = max(0, self.cfg.scan.max_analyses_per_day - self.store.analyses_today())
        selected = candidates[:min(self.cfg.scan.max_analyses_per_cycle, budget)]
        for sym, trig in candidates[len(selected):]:
            self.store.log("trigger_skipped_capacity", None, symbol=sym,
                           trigger=trig.trigger_type, severity=round(trig.severity, 4))

        out: list[str] = []
        for sym, trig in selected:
            try:
                pid = self._analyze(sym, trig, bars[sym], prices[sym])
                if pid:
                    out.append(pid)
            except Exception:
                log.exception("analysis failed for %s", sym)
        return out

    def _analyze(self, symbol: str, trig: Trigger, df, price: float) -> str | None:
        self.store.start_cooldown(symbol, trig.trigger_type,
                                  utcnow() + timedelta(days=self.cfg.triggers.cooldown_days))
        self.store.record_analysis()
        self.store.log("trigger_fired", None, symbol=symbol,
                       trigger=trig.trigger_type, **trig.details)

        packet = {
            "technicals": triggers.ta_snapshot(df, price),
            "fundamentals": fundamentals.snapshot(symbol),
            "news_headlines": fundamentals.news(symbol),
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
        return p.id if p.status is Status.PENDING else None
