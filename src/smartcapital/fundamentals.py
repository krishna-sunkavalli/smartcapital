"""Fundamentals + events + news from Financial Modeling Prep. The LLM only
ever sees data fetched here - it is never allowed to supply facts from memory."""
from __future__ import annotations

from datetime import date, datetime

import httpx

from smartcapital.config import secrets

BASE = "https://financialmodelingprep.com/api/v3"


def _get(path: str, **params):
    params["apikey"] = secrets().fmp_api_key
    r = httpx.get(f"{BASE}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def snapshot(symbol: str) -> dict:
    """One compact dict: profile, valuation, recent + upcoming earnings."""
    profile = (_get(f"profile/{symbol}") or [{}])[0]
    ratios = (_get(f"ratios-ttm/{symbol}") or [{}])[0]
    earnings = _get(f"historical/earning_calendar/{symbol}", limit=12) or []
    recent, upcoming = split_earnings(earnings, date.today())

    return {
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "market_cap": profile.get("mktCap"),
        "beta": profile.get("beta"),
        "pe_ttm": ratios.get("peRatioTTM"),
        "peg_ttm": ratios.get("pegRatioTTM"),
        "price_to_sales_ttm": ratios.get("priceToSalesRatioTTM"),
        "debt_to_equity": ratios.get("debtEquityRatioTTM"),
        "recent_earnings": recent[:4],
        "next_earnings_date": upcoming[0]["date"] if upcoming else None,
        "days_to_next_earnings": (
            (date.fromisoformat(upcoming[0]["date"]) - date.today()).days if upcoming else None),
        # Context flag: did this company report within the last 5 days? A
        # trigger right after a report usually IS the report's aftermath.
        "just_reported": _just_reported(recent, date.today()),
    }


def news(symbol: str, limit: int = 8) -> list[dict]:
    """Recent headlines for the symbol: date, title, source. Text only -
    the LLM weighs them; nothing here triggers anything."""
    rows = _get("stock_news", tickers=symbol, limit=limit) or []
    return [
        {"date": r.get("publishedDate"), "title": r.get("title"), "source": r.get("site")}
        for r in rows
    ]


def split_earnings(rows: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """FMP's per-symbol earning calendar mixes past reports (eps set) and
    scheduled future dates (eps null). Split into (recent desc, upcoming asc)."""
    recent, upcoming = [], []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        entry = {"date": d, "eps_actual": r.get("eps"), "eps_estimate": r.get("epsEstimated")}
        if date.fromisoformat(d) > today or r.get("eps") is None:
            if date.fromisoformat(d) >= today:
                upcoming.append(entry)
        else:
            recent.append(entry)
    recent.sort(key=lambda e: e["date"], reverse=True)
    upcoming.sort(key=lambda e: e["date"])
    return recent, upcoming


def _just_reported(recent: list[dict], today: date, within_days: int = 5) -> dict | None:
    if not recent:
        return None
    last = recent[0]
    days_ago = (today - date.fromisoformat(last["date"])).days
    if days_ago > within_days:
        return None
    beat = (last["eps_actual"] is not None and last["eps_estimate"] is not None
            and last["eps_actual"] >= last["eps_estimate"])
    return {"date": last["date"], "days_ago": days_ago,
            "eps_actual": last["eps_actual"], "eps_estimate": last["eps_estimate"],
            "beat_estimate": beat}
