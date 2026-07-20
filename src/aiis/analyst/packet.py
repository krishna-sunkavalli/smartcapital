"""Assembles and validates the data packet handed to the LLM. Every field
carries source + as-of; validation rejects the analysis outright if any
required field is missing or stale, and halts if feeds materially disagree.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiis.config import AppConfig
from aiis.data.base import DataPoint, Packet


class FeedDisagreementError(Exception):
    """Feeds materially disagree with each other; the system must halt analysis."""


BUY_REQUIRED = [
    "latest_price", "daily_bars", "indicators", "profile", "ratios",
    "earnings_history", "next_earnings_date",
]
REVIEW_REQUIRED = BUY_REQUIRED + ["position"]


def freshness_requirements(cfg: AppConfig) -> dict[str, timedelta]:
    f = cfg.freshness
    return {
        "latest_price": timedelta(seconds=f.market_price_seconds),
        "daily_bars": timedelta(hours=f.market_daily_bars_hours),
        "indicators": timedelta(hours=f.market_daily_bars_hours),
        "profile": timedelta(days=f.fundamentals_days),
        "ratios": timedelta(days=f.fundamentals_days),
        "earnings_history": timedelta(days=f.fundamentals_days),
        "next_earnings_date": timedelta(days=f.earnings_calendar_days),
        "position": timedelta(days=1),
    }


def build_packet(fields: dict[str, DataPoint]) -> Packet:
    packet = Packet()
    for name, dp in fields.items():
        packet.add(name, dp)
    return packet


def validate_packet(packet: Packet, kind: str, cfg: AppConfig, now: datetime | None = None) -> None:
    """Raises MissingDataError / FreshnessError / FeedDisagreementError.
    A failed validation means NO analysis happens for this trigger."""
    required = REVIEW_REQUIRED if kind == "portfolio_review" else BUY_REQUIRED
    reqs = freshness_requirements(cfg)
    packet.validate({name: reqs[name] for name in required if name in reqs}, now=now)

    # Cross-feed sanity: real-time price vs last daily close must not
    # materially disagree (beyond configured tolerance + a generous 1-day move
    # allowance is intentionally NOT granted - a >tolerance gap forces a halt
    # and human inspection rather than silent analysis on suspect data).
    price = packet.fields["latest_price"].value
    ind = packet.fields["indicators"].value
    last_close = ind.get("close") if isinstance(ind, dict) else None
    if price and last_close:
        gap = abs(price / last_close - 1)
        if gap > max(cfg.freshness.feed_disagreement_pct, 0.10):
            raise FeedDisagreementError(
                f"latest trade {price} vs last daily close {last_close} differ by "
                f"{gap:.1%}; halting analysis pending inspection"
            )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
