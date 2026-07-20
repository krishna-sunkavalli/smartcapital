from datetime import date

from smartcapital.fundamentals import _just_reported, split_earnings

TODAY = date(2026, 7, 20)

ROWS = [
    {"date": "2026-10-28", "eps": None, "epsEstimated": 2.10},   # scheduled
    {"date": "2026-07-29", "eps": None, "epsEstimated": 1.95},   # scheduled, 9 days out
    {"date": "2026-04-30", "eps": 1.88, "epsEstimated": 1.80},   # reported, beat
    {"date": "2026-01-29", "eps": 1.50, "epsEstimated": 1.60},   # reported, miss
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
