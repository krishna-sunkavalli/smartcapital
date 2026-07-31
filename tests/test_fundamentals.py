from datetime import date

from smartcapital import fundamentals
from smartcapital.fundamentals import (
    _bundled_nasdaq100,
    _bundled_sp500,
    _just_reported,
    split_earnings,
)

TODAY = date(2026, 7, 20)

ROWS = [
    {"date": "2026-10-28", "eps_actual": None, "eps_estimate": 2.10},   # scheduled
    {"date": "2026-07-29", "eps_actual": None, "eps_estimate": 1.95},   # scheduled, 9 days out
    {"date": "2026-04-30", "eps_actual": 1.88, "eps_estimate": 1.80},   # reported, beat
    {"date": "2026-01-29", "eps_actual": 1.50, "eps_estimate": 1.60},   # reported, miss
]


def test_split_earnings():
    recent, upcoming = split_earnings(ROWS, TODAY)
    assert [e["date"] for e in recent] == ["2026-04-30", "2026-01-29"]
    assert [e["date"] for e in upcoming] == ["2026-07-29", "2026-10-28"]


def test_next_earnings_days():
    _, upcoming = split_earnings(ROWS, TODAY)
    days = (date.fromisoformat(upcoming[0]["date"]) - TODAY).days
    assert days == 9


def test_just_reported_flag():
    recent = [{"date": "2026-07-17", "eps_actual": 2.0, "eps_estimate": 1.9}]
    flag = _just_reported(recent, TODAY)
    assert flag is not None and flag["days_ago"] == 3 and flag["beat_estimate"] is True

    old = [{"date": "2026-04-30", "eps_actual": 2.0, "eps_estimate": 1.9}]
    assert _just_reported(old, TODAY) is None


def test_just_reported_miss():
    recent = [{"date": "2026-07-18", "eps_actual": 1.5, "eps_estimate": 1.9}]
    assert _just_reported(recent, TODAY)["beat_estimate"] is False


def test_bundled_sp500_loads():
    symbols = _bundled_sp500()
    # A real S&P 500 snapshot: hundreds of tickers, sorted, deduped, no comments.
    assert len(symbols) > 400
    assert symbols == sorted(symbols)
    assert {"AAPL", "MSFT", "NVDA", "BRK.B"} <= set(symbols)
    assert not any(s.startswith("#") for s in symbols)


def test_sp500_symbols_returns_bundle():
    assert fundamentals.sp500_symbols() == _bundled_sp500()


def test_bundled_nasdaq100_loads():
    symbols = _bundled_nasdaq100()
    # A real NASDAQ-100 snapshot: ~100 tickers, sorted, deduped, no comments.
    assert 90 < len(symbols) < 120
    assert symbols == sorted(symbols)
    assert {"AAPL", "MSFT", "NVDA", "GOOGL"} <= set(symbols)
    assert not any(s.startswith("#") for s in symbols)


def test_nasdaq100_symbols_returns_bundle():
    assert fundamentals.nasdaq100_symbols() == _bundled_nasdaq100()


def test_news_date_parses_iso_and_none():
    assert fundamentals._news_date("2026-07-27T15:03:16Z") == "2026-07-27"
    assert fundamentals._news_date(None) is None


class _Boom:
    """Stands in for yf.Ticker to simulate a throttled/unavailable provider."""
    def __init__(self, *a, **k):
        raise RuntimeError("provider throttled")


def test_snapshot_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(fundamentals.yf, "Ticker", _Boom)
    fundamentals._cache.clear()
    snap = fundamentals.snapshot("ZZZZ")
    assert snap["sector"] is None
    assert snap["recent_earnings"] == []
    assert snap["next_earnings_date"] is None
    assert snap["just_reported"] is None


def test_news_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(fundamentals.yf, "Ticker", _Boom)
    fundamentals._cache.clear()
    assert fundamentals.news("ZZZZ") == []

