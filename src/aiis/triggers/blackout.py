"""Earnings blackout rule: no new BUY proposals within N trading days before a
scheduled earnings report. Earnings are a review trigger for held positions,
not a buy trigger.
"""
from __future__ import annotations

from datetime import date, timedelta


def trading_days_between(start: date, end: date) -> int:
    """Weekday count in (start, end]. Exchange holidays are treated as trading
    days, which only makes the blackout more conservative."""
    if end <= start:
        return 0
    days, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def in_earnings_blackout(today: date, next_earnings: date | None, blackout_trading_days: int) -> bool:
    if next_earnings is None:
        return False
    if next_earnings < today:
        return False
    return trading_days_between(today, next_earnings) <= blackout_trading_days
