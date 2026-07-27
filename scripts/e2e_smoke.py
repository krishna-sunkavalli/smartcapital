"""End-to-end smoke test for the SmartCapital pipeline.

Exercises the real chain against live Alpaca (paper), FMP, and Telegram:

    build data + verdict -> PENDING proposal -> Telegram send with buttons
    -> approval -> Alpaca paper limit order -> auto-cancel

Because the pipeline gates on an open market (both the scanner and the order
executor), this harness can optionally bypass that gate with --force-market so
the full chain can be validated outside market hours. It uses an isolated state
file so it never pollutes real cooldowns or the daily analysis budget.

Examples:
    # Non-interactive full chain (auto-approve, place + cancel a paper order):
    python scripts/e2e_smoke.py --symbol AAPL --place-order --force-market

    # Interactive: wait for you to tap Approve/Deny in Telegram:
    python scripts/e2e_smoke.py --symbol AAPL --interactive --place-order --force-market

    # Approval only, never touch Alpaca orders:
    python scripts/e2e_smoke.py --symbol AAPL
"""
from __future__ import annotations

import argparse
import asyncio
import os
import tempfile

from smartcapital import executor, triggers
from smartcapital.config import load_config
from smartcapital.engine import Engine
from smartcapital.state import Status, Store, utcnow
from smartcapital.telegram_bot import ApprovalBot
from smartcapital.triggers import Trigger

PASS, FAIL = "PASS", "FAIL"


def _line(stage: str, ok: bool, detail: str = "") -> str:
    return f"  [{PASS if ok else FAIL}] {stage}" + (f" - {detail}" if detail else "")


async def run(args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []

    cfg = load_config()
    state_path = os.path.join(tempfile.gettempdir(), f"smartcap-e2e-{os.getpid()}.json")
    store = Store(path=state_path)
    engine = Engine(cfg, store)

    if args.force_market:
        engine.market.market_open = lambda: True  # type: ignore[method-assign]

    # --- Stage 1: live data ---------------------------------------------------
    try:
        df = engine.market.daily_bars(args.symbol)
        price = engine.market.latest_price(args.symbol)
        ta = triggers.ta_snapshot(df, price)
        results.append(("Live Alpaca data (bars + price)", True,
                        f"{len(df)} bars, price ${price:,.2f}, day {ta['day_change_pct']}%"))
    except Exception as e:  # noqa: BLE001
        results.append(("Live Alpaca data (bars + price)", False, repr(e)))
        return _report(results, state_path)

    # --- Stage 2: analyze -> proposal (real FMP + stubbed verdict) ------------
    trig = Trigger("down_day", {"day_change_pct": ta["day_change_pct"]},
                   severity=abs(ta["day_change_pct"]))
    try:
        pid = engine._analyze(args.symbol, trig, df, price)
    except Exception as e:  # noqa: BLE001
        results.append(("Analyze -> proposal (FMP + verdict)", False, repr(e)))
        return _report(results, state_path)
    if not pid:
        results.append(("Analyze -> proposal (FMP + verdict)", False,
                        "verdict was DECLINE (no PENDING proposal)"))
        return _report(results, state_path)
    p = store.get(pid)
    results.append(("Analyze -> proposal (FMP + verdict)", True,
                    f"PENDING {p.symbol} qty={p.qty:g} band [{p.limit_low}, {p.limit_high}] "
                    f"model={p.llm_model}"))

    # --- Stage 3: Telegram send + approval ------------------------------------
    bot = ApprovalBot(store)
    async with bot.app:
        await bot.app.start()
        await bot.app.updater.start_polling(drop_pending_updates=True)
        try:
            await bot.send_proposal(pid)
            sent = store.get(pid).status is Status.PENDING
            results.append(("Telegram proposal sent (with Approve/Deny)", sent,
                            "check your Telegram" if sent else "send failed -> expired"))
            if not sent:
                return _report(results, state_path)

            if args.interactive:
                results.append((f"Awaiting your tap (up to {args.timeout}s)", True, ""))
                await _wait_decision(store, pid, args.timeout)
                st = store.get(pid).status
                results.append((f"Human decision received: {st.value}", st is not Status.PENDING,
                                "tap timed out" if st is Status.PENDING else ""))
                if st is not Status.APPROVED:
                    return await _finish(results, state_path, bot)
            else:
                ok = store.transition(p, Status.PENDING, Status.APPROVED)
                p.decided_at = utcnow()
                store.log("proposal_approved", p.id)
                results.append(("Auto-approve (simulated tap)", ok, "PENDING -> APPROVED"))

            # --- Stage 4: order placement -------------------------------------
            if args.place_order:
                placed = executor.execute(store, p, engine.market, cfg)
                if placed:
                    results.append(("Alpaca paper limit order submitted", True,
                                    f"broker_order_id={p.broker_order_id} "
                                    f"limit=${round(p.limit_high, 2)}"))
                    await bot.notify(f"E2E: paper order placed for {p.symbol} "
                                     f"(id {p.broker_order_id}); cancelling now.")
                    # Auto-cancel to leave a clean slate.
                    try:
                        engine.market.trading.cancel_order_by_id(p.broker_order_id)
                        results.append(("Auto-cancel paper order", True, "cancel requested"))
                        await bot.notify(f"E2E: paper order {p.broker_order_id} cancel requested.")
                    except Exception as e:  # noqa: BLE001
                        results.append(("Auto-cancel paper order", False, repr(e)))
                else:
                    reason = p.status_reason or (
                        "market gate (use --force-market)" if p.status is Status.APPROVED
                        else p.status.value)
                    results.append(("Alpaca paper limit order submitted", False, reason))
            else:
                results.append(("Order placement", True, "skipped (--place-order not set)"))

            return await _finish(results, state_path, bot)
        finally:
            await bot.app.updater.stop()
            await bot.app.stop()


async def _wait_decision(store: Store, pid: str, timeout: int) -> bool:
    for _ in range(timeout):
        if store.get(pid).status is not Status.PENDING:
            return store.get(pid).status is Status.APPROVED
        await asyncio.sleep(1)
    return False


async def _finish(results, path, _bot) -> int:
    return _report(results, path)


def _report(results, path) -> int:
    try:
        os.unlink(path)
    except OSError:
        pass
    print("\nEnd-to-end smoke test\n" + "=" * 40)
    for stage, ok, detail in results:
        print(_line(stage, ok, detail))
    failed = [s for s, ok, _ in results if not ok]
    print("=" * 40)
    print(f"RESULT: {'ALL PASS' if not failed else 'FAILED: ' + ', '.join(failed)}")
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--place-order", action="store_true",
                    help="place a real Alpaca paper order on approval, then auto-cancel it")
    ap.add_argument("--force-market", action="store_true",
                    help="bypass the market-open gate (needed outside market hours)")
    ap.add_argument("--interactive", action="store_true",
                    help="wait for a real Approve/Deny tap instead of auto-approving")
    ap.add_argument("--timeout", type=int, default=120, help="interactive tap timeout (seconds)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
