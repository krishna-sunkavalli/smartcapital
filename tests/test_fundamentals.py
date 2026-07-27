from datetime import date

import httpx

from smartcapital import fundamentals
from smartcapital.fundamentals import _bundled_sp500, _just_reported, split_earnings

TODAY = date(2026, 7, 20)

ROWS = [
    {"date": "2026-10-28", "epsActual": None, "epsEstimated": 2.10},   # scheduled
    {"date": "2026-07-29", "epsActual": None, "epsEstimated": 1.95},   # scheduled, 9 days out
    {"date": "2026-04-30", "epsActual": 1.88, "epsEstimated": 1.80},   # reported, beat
    {"date": "2026-01-29", "epsActual": 1.50, "epsEstimated": 1.60},   # reported, miss
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


def test_sp500_symbols_defaults_to_bundle(monkeypatch):
    def _boom(path, **params):  # must never be called in the default path
        raise AssertionError("live FMP endpoint hit when it should not be")

    monkeypatch.setattr(fundamentals, "_get", _boom)
    assert fundamentals.sp500_symbols() == _bundled_sp500()


def test_sp500_symbols_live_falls_back_on_402(monkeypatch, tmp_path):
    def _boom(path, **params):
        req = httpx.Request("GET", f"https://x/{path}")
        raise httpx.HTTPStatusError("paid", request=req,
                                    response=httpx.Response(402, request=req))

    monkeypatch.setattr(fundamentals, "_get", _boom)
    symbols = fundamentals.sp500_symbols(live=True, cache_dir=str(tmp_path))
    assert symbols == _bundled_sp500()

