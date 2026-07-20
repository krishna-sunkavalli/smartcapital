"""Command-line entry points.

  aiis init-db                  create tables
  aiis run                      run the full pipeline (scheduler + Telegram bot)
  aiis kill [--reason ...]      KILL SWITCH: disable all proposals immediately
  aiis unkill                   clear the kill switch
  aiis digest                   print the weekly approval digest
  aiis score                    score all logged recommendations vs control
  aiis phase1-report            promotion-criteria report for Phase 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from aiis.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aiis")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiis")
    parser.add_argument("--config", default="config/config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    runp = sub.add_parser("run")
    runp.add_argument("--watchlist", default="AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,CRM")
    killp = sub.add_parser("kill")
    killp.add_argument("--reason", default="manual kill switch")
    sub.add_parser("unkill")
    sub.add_parser("digest")
    scorep = sub.add_parser("score")
    scorep.add_argument("--horizon-days", type=int, default=21)
    ph1 = sub.add_parser("phase1-report")
    ph1.add_argument("--started", required=True, help="Phase 1 start date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    from aiis.db.session import get_session, init_db
    init_db()

    if args.cmd == "init-db":
        print("database initialized")
        return 0

    if args.cmd == "kill":
        from aiis.guardrails import killswitch
        with get_session() as s:
            killswitch.activate(s, args.reason)
        print("KILL SWITCH ACTIVE - all proposals disabled")
        return 0

    if args.cmd == "unkill":
        from aiis.guardrails import killswitch
        with get_session() as s:
            killswitch.deactivate(s)
        print("kill switch cleared")
        return 0

    if args.cmd == "digest":
        from aiis.approval.fatigue import weekly_digest
        with get_session() as s:
            d = weekly_digest(s, cfg.approval)
        print(json.dumps(d, indent=2))
        if d["gate_alarm"]:
            print("\n*** ALARM: approval rate near 100% - the human gate has "
                  "stopped functioning. Slow down and start rejecting. ***", file=sys.stderr)
        return 0

    if args.cmd == "score":
        from aiis.data.market import MarketData
        from aiis.evaluation.scoring import control_baseline, score_analyses, summarize
        market = MarketData()

        def lookup(symbol):
            try:
                return market.latest_price(symbol).value
            except Exception:
                return None

        with get_session() as s:
            llm = score_analyses(s, lookup, args.horizon_days)
            ctrl = control_baseline(s, lookup, args.horizon_days)
        print(json.dumps({"summary": summarize(llm, ctrl), "llm": llm, "control": ctrl},
                         indent=2, default=str))
        return 0

    if args.cmd == "phase1-report":
        from aiis.data.market import MarketData
        from aiis.evaluation.rollout import PromotionCriteria, evaluate_phase1
        from aiis.evaluation.scoring import control_baseline, score_analyses, summarize
        market = MarketData()

        def lookup(symbol):
            try:
                return market.latest_price(symbol).value
            except Exception:
                return None

        started = datetime.fromisoformat(args.started).replace(tzinfo=timezone.utc)
        with get_session() as s:
            summary = summarize(score_analyses(s, lookup), control_baseline(s, lookup))
            report = evaluate_phase1(s, summary, PromotionCriteria(), started)
        print(json.dumps(report, indent=2))
        return 0

    if args.cmd == "run":
        watchlist = [t.strip().upper() for t in args.watchlist.split(",") if t.strip()]
        asyncio.run(_run(cfg, watchlist))
        return 0
    return 1


async def _run(cfg, watchlist: list[str]) -> None:
    """Full pipeline: APScheduler drives scan / review / expiry / order sync;
    the Telegram bot handles approvals; the executor submits approved
    proposals. Everything resumes from persisted state after a restart."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from aiis.approval.telegram_bot import ApprovalBot, expire_stale_proposals
    from aiis.db.models import Proposal, ProposalStatus
    from aiis.db.session import get_session
    from aiis.execution.executor import execute_approved_proposal, sync_order_lifecycles
    from aiis.strategy.engine import Engine

    engine = Engine(cfg, watchlist)
    bot = ApprovalBot(cfg)

    def scan_job():
        with get_session() as s:
            proposals = engine.scan_buy_side(s) + engine.review_positions(s)
            ids = [p.id for p in proposals]
        for pid in ids:
            asyncio.get_event_loop().create_task(bot.send_proposal(pid))

    def execute_job():
        from aiis.approval.fatigue import daily_cap_reached
        with get_session() as s:
            if daily_cap_reached(s, cfg.approval, cfg.rollout):
                return
            approved = s.query(Proposal).filter(Proposal.status == ProposalStatus.APPROVED).all()
            for p in approved:
                ctx = engine.build_guardrail_context(s, p.symbol)
                execute_approved_proposal(s, p, ctx, cfg)

    def maintenance_job():
        expire_stale_proposals()
        with get_session() as s:
            sync_order_lifecycles(s)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(scan_job, "interval", minutes=cfg.triggers.poll_interval_minutes)
    scheduler.add_job(execute_job, "interval", seconds=30)
    scheduler.add_job(maintenance_job, "interval", minutes=1)
    scheduler.start()

    log.info("aiis running: watchlist=%s phase=%d poll=%dmin",
             watchlist, cfg.rollout.phase, cfg.triggers.poll_interval_minutes)
    async with bot.app:
        await bot.app.start()
        await bot.app.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await bot.app.updater.stop()
            await bot.app.stop()


if __name__ == "__main__":
    raise SystemExit(main())
