"""The strategy engine: the deterministic pipeline that owns monitoring and
wiring. One pass = scan -> triggers -> dedup/blackout -> packet -> adversarial
analysis -> guardrail-checked proposal -> Telegram. All state is in the
database; a restart mid-flow resumes exactly where persisted state says.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aiis.analyst.adversarial import run_analysis
from aiis.analyst.base_rate import check_buy_base_rate
from aiis.analyst.llm import LLM
from aiis.analyst.packet import build_packet
from aiis.config import AppConfig
from aiis.data.base import DataPoint, MissingDataError, FreshnessError
from aiis.data.events import StructuredEvents
from aiis.data.fundamentals import Fundamentals
from aiis.data.market import MarketData
from aiis.db.models import (
    Analysis, Position, Proposal, ProposalStatus, Recommendation, TriggerEvent, utcnow,
)
from aiis.execution.audit import append_audit
from aiis.guardrails import killswitch
from aiis.guardrails.engine import GuardrailContext, ProposedOrder, check_all
from aiis.triggers.blackout import in_earnings_blackout
from aiis.triggers.buy_triggers import detect_buy_triggers
from aiis.triggers.dedup import admit_trigger
from aiis.triggers.indicators import indicator_snapshot
from aiis.triggers.review_triggers import detect_review_triggers

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: AppConfig, watchlist: list[str]) -> None:
        self.cfg = cfg
        self.watchlist = watchlist
        self.market = MarketData()
        self.fundamentals = Fundamentals()
        self.events = StructuredEvents()
        self.llm = LLM(cfg.analyst)

    # ------------------------------------------------------------------ scan
    def scan_buy_side(self, session: Session) -> list[Proposal]:
        """One polling cycle over the watchlist."""
        proposals: list[Proposal] = []
        if killswitch.is_active(session):
            log.info("kill switch active; skipping scan")
            return proposals
        sp500 = self.fundamentals.sp500_constituents(self.cfg.universe.constituents_cache_days)
        for symbol in self.watchlist:
            if symbol not in sp500:
                continue
            try:
                proposals += self._scan_symbol(session, symbol)
            except (MissingDataError, FreshnessError) as e:
                append_audit(session, "analysis_rejected_data", None,
                             {"symbol": symbol, "error": str(e)})
            except Exception:
                log.exception("scan failed for %s", symbol)
        check_buy_base_rate(session, self.cfg.analyst)
        return proposals

    def _scan_symbol(self, session: Session, symbol: str) -> list[Proposal]:
        df, bars_dp = self.market.daily_bars(symbol)
        if bars_dp.value is None:
            return []
        triggers = detect_buy_triggers(df, self.cfg.triggers.buy)
        if not triggers:
            return []

        # Earnings blackout: no new buy proposals near a scheduled report.
        next_earnings = self.events.next_earnings_date(symbol)
        if next_earnings.value and in_earnings_blackout(
                date.today(), date.fromisoformat(next_earnings.value),
                self.cfg.triggers.earnings_blackout_trading_days):
            append_audit(session, "trigger_suppressed_blackout", None,
                         {"symbol": symbol, "next_earnings": next_earnings.value})
            return []

        out = []
        for trig in triggers:
            event = admit_trigger(session, symbol, trig,
                                  self.cfg.triggers.cooldown_days_per_symbol_trigger)
            if event is None:
                continue  # cooldown - the LLM is never invoked
            # Control baseline: log the no-LLM outcome ("alert would have been
            # sent here") BEFORE the LLM runs, at the same reference price.
            price_dp = self.market.latest_price(symbol)
            append_audit(session, "control_baseline_alert", event.id, {
                "symbol": symbol, "trigger": trig.trigger_type,
                "price": price_dp.value, "as_of": price_dp.as_of.isoformat(),
            })
            analysis = self._analyze(session, event, df, bars_dp, price_dp, kind="buy")
            if analysis and analysis.recommendation is Recommendation.BUY:
                p = self._propose(session, analysis)
                if p:
                    out.append(p)
        return out

    # ---------------------------------------------------------------- review
    def review_positions(self, session: Session) -> list[Proposal]:
        """The sell/portfolio-review loop over every open system position."""
        out: list[Proposal] = []
        if killswitch.is_active(session):
            return out
        positions = session.query(Position).filter(Position.closed_at.is_(None)).all()
        managed = sum(p.qty * self.market.latest_price(p.symbol).value for p in positions) \
            if positions else 0.0
        for pos in positions:
            df, bars_dp = self.market.daily_bars(pos.symbol)
            price_dp = self.market.latest_price(pos.symbol)
            snapshot = indicator_snapshot(df) if bars_dp.value else {}
            origin = session.get(Analysis, pos.origin_analysis_id) if pos.origin_analysis_id else None
            triggers = detect_review_triggers(
                pos, price_dp.value, managed, self.cfg.triggers,
                last_weekly_review=self._last_review(session, pos.symbol),
                earnings_released=self.events.earnings_released_since(
                    pos.symbol, utcnow() - timedelta(days=7)),
                origin_analysis=origin,
                indicator_snapshot=snapshot,
            )
            for trig in triggers:
                event = admit_trigger(session, pos.symbol, trig,
                                      self.cfg.triggers.cooldown_days_per_symbol_trigger)
                if event is None:
                    continue
                analysis = self._analyze(session, event, df, bars_dp, price_dp,
                                         kind="portfolio_review", position=pos)
                if analysis and analysis.recommendation in (Recommendation.TRIM, Recommendation.SELL):
                    p = self._propose(session, analysis, position=pos)
                    if p:
                        out.append(p)
        return out

    def _last_review(self, session: Session, symbol: str) -> datetime | None:
        return session.execute(
            select(func.max(TriggerEvent.created_at)).where(
                TriggerEvent.symbol == symbol, TriggerEvent.trigger_type == "weekly_review")
        ).scalar_one_or_none()

    # --------------------------------------------------------------- analyze
    def _analyze(self, session: Session, event: TriggerEvent, df, bars_dp: DataPoint,
                 price_dp: DataPoint, kind: str, position: Position | None = None) -> Analysis | None:
        symbol = event.symbol
        snapshot = indicator_snapshot(df, self.cfg.triggers.buy.ema_fast, self.cfg.triggers.buy.ema_slow)
        fields = {
            "latest_price": price_dp,
            "daily_bars": bars_dp,
            "indicators": DataPoint(value=snapshot, source="alpaca", as_of=bars_dp.as_of),
            "profile": self.fundamentals.profile(symbol),
            "ratios": self.fundamentals.ratios(symbol),
            "earnings_history": self.fundamentals.earnings_history(symbol),
            "next_earnings_date": self.events.next_earnings_date(symbol),
        }
        if position is not None:
            fields["position"] = DataPoint(
                value={"qty": position.qty, "avg_entry_price": position.avg_entry_price,
                       "opened_at": position.opened_at.isoformat(),
                       "pnl_pct": round((price_dp.value / position.avg_entry_price - 1) * 100, 2),
                       "original_thesis_conditions": (
                           session.get(Analysis, position.origin_analysis_id).thesis_conditions
                           if position.origin_analysis_id else [])},
                source="aiis-db", as_of=utcnow())
        return run_analysis(session, self.llm, self.cfg, event, build_packet(fields), kind)

    # --------------------------------------------------------------- propose
    def _propose(self, session: Session, analysis: Analysis,
                 position: Position | None = None) -> Proposal | None:
        """Turn an actionable recommendation into a guardrail-checked, price-
        banded proposal. Guardrail failure here means no proposal is ever
        shown to the human."""
        cfg = self.cfg
        ref = analysis.hypothetical_entry_price
        judge = analysis.judge_output or {}
        account = self.market.account()
        managed_cap = account["equity"] * cfg.exposure.managed_capital_pct

        if analysis.recommendation is Recommendation.BUY:
            action = "buy"
            size_pct = min(float(judge.get("proposed_size_pct_of_managed_capital") or 2.0), 10.0)
            notional = managed_cap * size_pct / 100.0
            if cfg.rollout.phase == 2:
                notional = min(notional, cfg.rollout.phase2_max_order_notional)
            qty = max(1, int(notional // ref))
        else:  # trim | sell of an existing position
            action = analysis.recommendation.value
            if position is None:
                return None
            if action == "trim":
                trim_pct = min(max(float(judge.get("proposed_trim_pct") or 25.0), 1.0), 99.0)
                qty = max(1, int(position.qty * trim_pct / 100.0))
            else:
                qty = int(position.qty)
        notional = qty * ref

        band = cfg.orders.price_band_pct
        order = ProposedOrder(
            symbol=analysis.symbol, action=action, qty=float(qty), notional=notional,
            reference_price=ref, limit_low=round(ref * (1 - band), 2),
            limit_high=round(ref * (1 + band), 2),
            sector=(session.get(Position, analysis.symbol).sector
                    if session.get(Position, analysis.symbol) else
                    (self.fundamentals.profile(analysis.symbol).value or {}).get("sector", "unknown")),
        )
        ctx = self.build_guardrail_context(session, symbol=analysis.symbol, latest_price=ref)
        violations = check_all(order, ctx, cfg)
        proposal = Proposal(
            analysis_id=analysis.id, symbol=analysis.symbol, action=action,
            qty=float(qty), notional=notional, reference_price=ref,
            limit_low=order.limit_low, limit_high=order.limit_high,
            expires_at=utcnow() + timedelta(minutes=cfg.approval.ttl_minutes),
            guardrail_report={"violations": [vars(v) for v in violations]},
        )
        if violations:
            proposal.status = ProposalStatus.VOIDED
            proposal.status_reason = "; ".join(f"{v.rule}: {v.detail}" for v in violations)
        session.add(proposal)
        session.flush()
        append_audit(session, "proposal_created", proposal.id, {
            "action": action, "qty": qty, "band": [order.limit_low, order.limit_high],
            "voided": bool(violations),
        })
        return None if violations else proposal

    # ----------------------------------------------------------- ctx builder
    def build_guardrail_context(self, session: Session, symbol: str,
                                latest_price: float | None = None) -> GuardrailContext:
        now = utcnow()
        account = self.market.account()
        clock = self.market.clock()
        positions = {
            p.symbol: {"qty": p.qty, "sector": p.sector,
                       "notional": p.qty * (latest_price if p.symbol == symbol and latest_price
                                            else self.market.latest_price(p.symbol).value)}
            for p in session.query(Position).filter(Position.closed_at.is_(None))
        }
        day_ago, week_ago, hour_ago = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(hours=1)

        def deployed_since(ts):
            return session.execute(
                select(func.coalesce(func.sum(Proposal.notional), 0.0)).where(
                    Proposal.action == "buy",
                    Proposal.status.in_([ProposalStatus.APPROVED, ProposalStatus.EXECUTED]),
                    Proposal.created_at >= ts)
            ).scalar_one()

        def proposals_since(ts):
            return session.execute(
                select(func.count(Proposal.id)).where(Proposal.created_at >= ts)).scalar_one()

        mins_since_open = mins_to_close = None
        if clock["is_open"]:
            mins_to_close = (clock["next_close"] - clock["now"]).total_seconds() / 60
            mins_since_open = 6.5 * 60 - mins_to_close  # regular session length

        return GuardrailContext(
            now=now,
            account_equity=account["equity"],
            available_cash=account["cash"],
            sp500_symbols=self.fundamentals.sp500_constituents(),
            positions=positions,
            deployed_today=deployed_since(day_ago),
            deployed_this_week=deployed_since(week_ago),
            proposals_last_hour=proposals_since(hour_ago),
            proposals_last_day=proposals_since(day_ago),
            latest_price=latest_price if latest_price is not None
            else self.market.latest_price(symbol).value,
            market_is_open=clock["is_open"],
            minutes_since_open=mins_since_open,
            minutes_to_close=mins_to_close,
            kill_switch_active=killswitch.is_active(session),
        )
