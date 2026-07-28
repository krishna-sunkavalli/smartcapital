"""The pipeline, exactly the v1 flow, at S&P-500 scale:

batch-fetch bars + prices for the whole universe -> detect triggers ->
rank by severity and apply per-cycle/daily caps -> gather TA + fundamentals
-> LLM buy/decline -> if buy, Telegram approval -> if approved, limit order.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from smartcapital import analyst, fundamentals, telemetry, triggers
from smartcapital.config import Config
from smartcapital.market import Market
from smartcapital.state import Proposal, Status, Store, utcnow
from smartcapital.triggers import Trigger

log = logging.getLogger(__name__)

# Floor for the liquidity proxy so an illiquid/halted name (zero or NaN volume)
# still ranks on its severity rather than collapsing to zero.
_MIN_DOLLAR_VOLUME = 1_000_000.0


def _rank_score(trig: Trigger, df, price: float) -> float:
    """Ranking key for the scarce analysis budget: trigger severity weighted by
    a size/liquidity proxy so a deep drop in an obscure micro-cap doesn't crowd
    out a meaningful drop in a heavily-traded mega-cap. Dollar volume
    (price x 20-day average volume) is a keyless size proxy already implied by
    the bars; log-scaling bounds the boost to roughly 1.7x from the smallest to
    the largest S&P 500 name, so severity still dominates within a size tier."""
    try:
        avg_vol = float(df["volume"].tail(20).mean())
    except Exception:
        avg_vol = 0.0
    if math.isnan(avg_vol):
        avg_vol = 0.0
    dollar_vol = max(price * avg_vol, _MIN_DOLLAR_VOLUME)
    return trig.severity * math.log10(dollar_vol)


class Engine:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.market = Market()

    def universe(self) -> list[str]:
        if isinstance(self.cfg.watchlist, list):
            return self.cfg.watchlist
        if self.cfg.watchlist == "sp500":
            return fundamentals.sp500_symbols(
                cache_days=self.cfg.scan.universe_cache_days,
            )
        return [self.cfg.watchlist]

    def scan(self) -> list[str]:
        """One polling cycle. Returns ids of proposals awaiting approval."""
        if not self.market.market_open():
            log.info("scan skipped: market closed")
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

        log.info("scan: %d symbols, %d bars, %d prices, %d candidates",
                 len(symbols), len(bars), len(prices), len(candidates))

        # Phase 2: rank by size-weighted severity and cap - the human gate is
        # the scarce resource. Skipped candidates get NO cooldown, so a
        # still-valid trigger re-competes next cycle.
        candidates.sort(key=lambda c: _rank_score(c[1], bars[c[0]], prices[c[0]]),
                        reverse=True)
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
        # Gather data and get the verdict FIRST. Cooldown + daily-budget are only
        # committed after a successful verdict, so a transient FMP/LLM error lets
        # the symbol re-compete next cycle instead of burning the slot.
        packet = {
            "technicals": triggers.ta_snapshot(df, price),
            "fundamentals": fundamentals.snapshot(symbol),
            "news_headlines": fundamentals.news(symbol),
        }
        verdict = analyst.analyze(symbol, trig.trigger_type, trig.details,
                                  packet, self.cfg.llm)

        telemetry.record_llm_usage(verdict.get("usage"), verdict.get("model", self.cfg.llm.model))

        self.store.start_cooldown(symbol, trig.trigger_type,
                                  utcnow() + timedelta(days=self.cfg.triggers.cooldown_days))
        self.store.record_analysis()
        self.store.log("trigger_fired", None, symbol=symbol,
                       trigger=trig.trigger_type, **trig.details)

        band = self.cfg.order.price_band_pct
        # Whole shares only: a single share must fit inside the per-trade
        # notional, otherwise buying even one share overspends the budget. Such
        # a buy is recorded as VOIDED (non-actionable) rather than forced to 1.
        qty = int(self.cfg.order.notional_usd // price)
        is_buy = verdict["recommendation"] == "buy"
        unaffordable = is_buy and qty < 1
        if unaffordable:
            self.store.log("buy_unaffordable", None, symbol=symbol,
                           price=round(price, 2), notional=self.cfg.order.notional_usd)
        if is_buy and not unaffordable:
            status = Status.PENDING
        elif is_buy:
            status = Status.VOIDED
        else:
            status = Status.DECLINED
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
            status=status,
            expires_at=(utcnow() + timedelta(minutes=self.cfg.approval.ttl_minutes)
                        if status is Status.PENDING else None),
        ))
        self.store.log("llm_" + verdict["recommendation"], p.id, symbol=symbol,
                       confidence=verdict.get("confidence"),
                       reasoning=verdict.get("reasoning"))
        telemetry.record_decision(symbol, verdict["recommendation"])
        return p.id if p.status is Status.PENDING else None
