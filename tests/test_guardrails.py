from datetime import datetime, timezone

import pytest

from aiis.guardrails.engine import GuardrailContext, ProposedOrder, check_all


@pytest.fixture
def ctx():
    return GuardrailContext(
        now=datetime.now(timezone.utc),
        account_equity=50_000.0,
        available_cash=10_000.0,
        sp500_symbols={"MSFT", "AAPL", "NVDA"},
        positions={},
        deployed_today=0.0,
        deployed_this_week=0.0,
        proposals_last_hour=0,
        proposals_last_day=0,
        latest_price=100.0,
        market_is_open=True,
        minutes_since_open=120.0,
        minutes_to_close=200.0,
    )


def order(**kw):
    base = dict(symbol="MSFT", action="buy", qty=5.0, notional=500.0,
                reference_price=100.0, limit_low=99.0, limit_high=101.0,
                sector="Technology")
    base.update(kw)
    return ProposedOrder(**base)


def rules(violations):
    return {v.rule for v in violations}


def test_clean_order_passes(ctx, cfg):
    assert check_all(order(), ctx, cfg) == []


def test_non_sp500_rejected(ctx, cfg):
    assert "universe" in rules(check_all(order(symbol="GME"), ctx, cfg))


def test_kill_switch_blocks_everything(ctx, cfg):
    ctx.kill_switch_active = True
    assert "kill_switch" in rules(check_all(order(), ctx, cfg))


def test_price_outside_band_voids(ctx, cfg):
    ctx.latest_price = 103.0
    assert "price_band" in rules(check_all(order(), ctx, cfg))


def test_auction_window_blocked(ctx, cfg):
    ctx.minutes_since_open = 3.0
    assert "auction_window" in rules(check_all(order(), ctx, cfg))


def test_position_size_limit(ctx, cfg):
    # managed cap = 50k * 20% = 10k; per-name cap = 10% of that = 1k
    assert "position_size" in rules(check_all(order(qty=15, notional=1500.0), ctx, cfg))


def test_sector_and_cluster_limits(ctx, cfg):
    # sector cap = 30% of 10k = 3k; existing 2.8k tech + 0.5k order breaches it
    ctx.positions = {"AAPL": {"qty": 20, "sector": "Technology", "notional": 2800.0}}
    r = rules(check_all(order(notional=500.0, qty=5), ctx, cfg))
    assert "sector_exposure" in r


def test_correlated_cluster_limit(ctx, cfg):
    # cluster cap = 45% of 10k = 4.5k across tech-ish sectors
    ctx.positions = {
        "AAPL": {"qty": 10, "sector": "Technology", "notional": 2500.0},
        "AMZN": {"qty": 10, "sector": "Consumer Cyclical", "notional": 2100.0},
    }
    r = rules(check_all(order(symbol="NVDA", notional=900.0, qty=9), ctx, cfg))
    assert "correlated_cluster" in r


def test_daily_deployment_limit(ctx, cfg):
    ctx.deployed_today = 1900.0
    assert "daily_deployment" in rules(check_all(order(notional=200.0, qty=2), ctx, cfg))


def test_cash_buffer(ctx, cfg):
    ctx.available_cash = 900.0
    assert "available_cash" in rules(check_all(order(notional=500.0), ctx, cfg))


def test_anomaly_halt_on_proposal_burst(ctx, cfg):
    ctx.proposals_last_hour = cfg.safety.max_proposals_per_rolling_hour
    assert "anomaly_halt" in rules(check_all(order(), ctx, cfg))


def test_sell_cannot_exceed_held_qty(ctx, cfg):
    ctx.positions = {"MSFT": {"qty": 3.0, "sector": "Technology", "notional": 300.0}}
    v = rules(check_all(order(action="sell", qty=5.0), ctx, cfg))
    assert "instrument" in v


def test_phase2_notional_cap(ctx, cfg):
    cfg.rollout.phase = 2
    v = rules(check_all(order(qty=6, notional=600.0), ctx, cfg))
    assert "phase2_limit" in v
