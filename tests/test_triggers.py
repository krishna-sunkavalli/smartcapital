from datetime import date, datetime, timedelta, timezone

from aiis.config import TriggersCfg
from aiis.db.models import Analysis, Position, Recommendation
from aiis.triggers.blackout import in_earnings_blackout, trading_days_between
from aiis.triggers.buy_triggers import Trigger
from aiis.triggers.dedup import admit_trigger
from aiis.triggers.review_triggers import detect_review_triggers


def test_trading_days_skip_weekends():
    # Fri 2026-07-17 -> Mon 2026-07-20 is 1 trading day
    assert trading_days_between(date(2026, 7, 17), date(2026, 7, 20)) == 1


def test_earnings_blackout_window():
    today = date(2026, 7, 20)  # Monday
    assert in_earnings_blackout(today, date(2026, 7, 24), 5)      # 4 trading days out
    assert not in_earnings_blackout(today, date(2026, 8, 20), 5)  # far out
    assert not in_earnings_blackout(today, None, 5)


def test_cooldown_suppresses_refires(session):
    trig = Trigger("ema_20_50_cross", "buy", {})
    first = admit_trigger(session, "MSFT", trig, cooldown_days=5)
    assert first is not None
    assert admit_trigger(session, "MSFT", trig, cooldown_days=5) is None
    # Different trigger type on same symbol is independent
    assert admit_trigger(session, "MSFT", Trigger("rsi_oversold", "buy", {}), 5) is not None
    # After the cooldown elapses it fires again
    later = datetime.now(timezone.utc) + timedelta(days=6)
    assert admit_trigger(session, "MSFT", trig, cooldown_days=5, now=later) is not None


def _pos(entry=100.0, qty=10.0):
    return Position(symbol="MSFT", qty=qty, avg_entry_price=entry, sector="Technology")


def test_drawdown_review_trigger():
    cfg = TriggersCfg()
    trigs = detect_review_triggers(_pos(entry=100.0), current_price=85.0,
                                   managed_capital=10_000.0, cfg=cfg,
                                   now=datetime(2026, 7, 21, tzinfo=timezone.utc))  # Tuesday
    assert "drawdown" in {t.trigger_type for t in trigs}


def test_concentration_review_trigger():
    cfg = TriggersCfg()
    trigs = detect_review_triggers(_pos(qty=20.0), current_price=100.0,
                                   managed_capital=10_000.0, cfg=cfg,
                                   now=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert "concentration" in {t.trigger_type for t in trigs}


def test_weekly_review_fires_on_configured_day():
    cfg = TriggersCfg(weekly_review_day="friday")
    friday = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
    trigs = detect_review_triggers(_pos(), 100.0, 100_000.0, cfg, now=friday)
    assert "weekly_review" in {t.trigger_type for t in trigs}
    tuesday = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
    trigs = detect_review_triggers(_pos(), 100.0, 100_000.0, cfg, now=tuesday)
    assert "weekly_review" not in {t.trigger_type for t in trigs}


def test_thesis_break_check():
    cfg = TriggersCfg()
    origin = Analysis(
        trigger_event_id="t", symbol="MSFT", kind="buy", model="m", prompt_version="2.0.0",
        temperature=0.0, data_packet={}, bear_case="", bull_case="", judge_output={},
        recommendation=Recommendation.BUY, hypothetical_entry_price=100.0,
        thesis_conditions=[{"metric": "price", "op": "gt", "value": 95.0}],
    )
    trigs = detect_review_triggers(_pos(), current_price=90.0, managed_capital=100_000.0,
                                   cfg=cfg, now=datetime(2026, 7, 21, tzinfo=timezone.utc),
                                   origin_analysis=origin, indicator_snapshot={"rsi14": 40.0})
    assert "thesis_break" in {t.trigger_type for t in trigs}
    # Condition still holding -> no thesis break
    trigs = detect_review_triggers(_pos(), current_price=98.0, managed_capital=100_000.0,
                                   cfg=cfg, now=datetime(2026, 7, 21, tzinfo=timezone.utc),
                                   origin_analysis=origin, indicator_snapshot={"rsi14": 40.0})
    assert "thesis_break" not in {t.trigger_type for t in trigs}
