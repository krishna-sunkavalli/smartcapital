from datetime import datetime, timedelta, timezone

import pytest

from aiis.analyst.packet import FeedDisagreementError, build_packet, validate_packet
from aiis.config import AppConfig
from aiis.data.base import DataPoint, FreshnessError, MissingDataError

NOW = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)


def fresh(value, seconds_old=10, source="alpaca"):
    return DataPoint(value=value, source=source, as_of=NOW - timedelta(seconds=seconds_old))


def full_fields(price=100.0, close=100.0):
    return {
        "latest_price": fresh(price),
        "daily_bars": fresh(300),
        "indicators": fresh({"close": close, "rsi14": 45.0}),
        "profile": fresh({"sector": "Technology"}, source="fmp"),
        "ratios": fresh({"pe_ttm": 30.0}, source="fmp"),
        "earnings_history": fresh([{"date": "2026-04-30"}], source="fmp"),
        "next_earnings_date": fresh("2026-08-05", source="fmp-earnings-calendar"),
    }


def test_valid_packet_passes(cfg):
    validate_packet(build_packet(full_fields()), "buy", cfg, now=NOW)


def test_missing_field_rejects(cfg):
    fields = full_fields()
    del fields["ratios"]
    with pytest.raises(MissingDataError):
        validate_packet(build_packet(fields), "buy", cfg, now=NOW)


def test_none_value_rejects(cfg):
    fields = full_fields()
    fields["ratios"] = fresh(None, source="fmp")
    with pytest.raises(MissingDataError):
        validate_packet(build_packet(fields), "buy", cfg, now=NOW)


def test_stale_price_rejects(cfg):
    fields = full_fields()
    fields["latest_price"] = fresh(100.0, seconds_old=cfg.freshness.market_price_seconds + 60)
    with pytest.raises(FreshnessError):
        validate_packet(build_packet(fields), "buy", cfg, now=NOW)


def test_feed_disagreement_halts(cfg):
    with pytest.raises(FeedDisagreementError):
        validate_packet(build_packet(full_fields(price=130.0, close=100.0)), "buy", cfg, now=NOW)


def test_review_requires_position(cfg):
    with pytest.raises(MissingDataError):
        validate_packet(build_packet(full_fields()), "portfolio_review", cfg, now=NOW)
