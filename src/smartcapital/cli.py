"""Entry point: `smartcapital run` starts the pipeline (scheduler + Telegram)."""
from __future__ import annotations

import argparse
import asyncio
import logging

from smartcapital.config import load_config, secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("smartcapital")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smartcapital")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    parser.parse_args(argv)
    asyncio.run(_run())
    return 0


async def _run() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from smartcapital.engine import Engine
    from smartcapital.executor import execute, sync_orders
    from smartcapital.state import Status, Store
    from smartcapital.telegram_bot import ApprovalBot, expire_stale

    cfg = load_config()
    store = Store()
    engine = Engine(cfg, store)
    bot = ApprovalBot(store)
    loop = asyncio.get_running_loop()

    def scan_job():
        for pid in engine.scan():
            asyncio.run_coroutine_threadsafe(bot.send_proposal(pid), loop)

    def execute_job():
        for p in store.with_status(Status.APPROVED):
            if execute(store, p, engine.market, cfg):
                asyncio.run_coroutine_threadsafe(
                    bot.notify(f"📤 Limit order submitted: {p.qty:g} {p.symbol} "
                               f"@ ≤ ${p.limit_high:,.2f}"), loop)

    def maintenance_job():
        expire_stale(store)
        for symbol, outcome in sync_orders(store, engine.market):
            asyncio.run_coroutine_threadsafe(bot.notify(f"📦 {symbol}: {outcome}"), loop)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(scan_job, "interval", minutes=cfg.triggers.poll_interval_minutes)
    scheduler.add_job(execute_job, "interval", seconds=30)
    scheduler.add_job(maintenance_job, "interval", minutes=1)
    scheduler.start()

    log.info("smartcapital running: watchlist=%s poll=%dmin env=%s",
             cfg.watchlist, cfg.triggers.poll_interval_minutes, secrets().alpaca_env)
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
