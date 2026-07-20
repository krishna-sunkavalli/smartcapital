"""Entry points:

  smartcapital run       full pipeline (scheduler + Telegram bot)
  smartcapital kill      disable all new proposals immediately
  smartcapital unkill    re-enable
  smartcapital status    open proposals + recent events
  smartcapital init-db   create tables
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from smartcapital.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("smartcapital")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smartcapital")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("kill")
    sub.add_parser("unkill")
    sub.add_parser("status")
    sub.add_parser("init-db")
    args = parser.parse_args(argv)

    from smartcapital import db
    db.init_db()

    if args.cmd == "init-db":
        print("database initialized")
    elif args.cmd == "kill":
        with db.session() as s:
            db.set_kill_switch(s, True)
        print("KILL SWITCH ON - no new proposals will be created or executed")
    elif args.cmd == "unkill":
        with db.session() as s:
            db.set_kill_switch(s, False)
        print("kill switch off")
    elif args.cmd == "status":
        _status(db)
    elif args.cmd == "run":
        asyncio.run(_run())
    return 0


def _status(db) -> None:
    from sqlalchemy import select
    from smartcapital.db import Event, Proposal
    with db.session() as s:
        print("kill switch:", "ON" if db.kill_switch_on(s) else "off")
        for p in s.execute(select(Proposal).order_by(Proposal.created_at.desc()).limit(10)).scalars():
            print(f"{p.created_at:%m-%d %H:%M} {p.symbol:6} {p.trigger_type:14} "
                  f"{p.status.value:9} {p.status_reason or ''}")
        print("--- recent events ---")
        for e in s.execute(select(Event).order_by(Event.seq.desc()).limit(10)).scalars():
            print(f"{e.at:%m-%d %H:%M} {e.kind:22} {e.payload}")


async def _run() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from smartcapital import db
    from smartcapital.engine import Engine
    from smartcapital.executor import execute, sync_orders
    from smartcapital.telegram_bot import ApprovalBot, expire_stale

    cfg = load_config()
    engine = Engine(cfg)
    bot = ApprovalBot()
    loop = asyncio.get_running_loop()

    def scan_job():
        with db.session() as s:
            new_ids = engine.scan(s)
        for pid in new_ids:
            asyncio.run_coroutine_threadsafe(bot.send_proposal(pid), loop)

    def execute_job():
        with db.session() as s:
            for p in db.approved_proposals(s):
                if execute(s, p, engine.market, cfg):
                    asyncio.run_coroutine_threadsafe(
                        bot.notify(f"📤 Limit order submitted: {p.qty:g} {p.symbol} "
                                   f"@ ≤ ${p.limit_high:,.2f}"), loop)

    def maintenance_job():
        expire_stale()
        with db.session() as s:
            for symbol, outcome in sync_orders(s, engine.market):
                asyncio.run_coroutine_threadsafe(
                    bot.notify(f"📦 {symbol}: {outcome}"), loop)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(scan_job, "interval", minutes=cfg.triggers.poll_interval_minutes)
    scheduler.add_job(execute_job, "interval", seconds=30)
    scheduler.add_job(maintenance_job, "interval", minutes=1)
    scheduler.start()

    log.info("smartcapital running: watchlist=%s poll=%dmin env=%s",
             cfg.watchlist, cfg.triggers.poll_interval_minutes,
             __import__("smartcapital.config", fromlist=["secrets"]).secrets().alpaca_env)
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
