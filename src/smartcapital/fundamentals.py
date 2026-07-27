"""Fundamentals + events + news from Financial Modeling Prep. The LLM only
ever sees data fetched here - it is never allowed to supply facts from memory."""
from __future__ import annotations

import json
from datetime import date

import httpx

from smartcapital.config import secrets

# FMP retired the /api/v3 "legacy" endpoints; the current API lives under /stable
# and takes the symbol as a query parameter (e.g. profile?symbol=AAPL).
BASE = "https://financialmodelingprep.com/stable"


def _get(path: str, **params):
    params["apikey"] = secrets().fmp_api_key
    r = httpx.get(f"{BASE}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def snapshot(symbol: str) -> dict:
    """One compact dict: profile, valuation, recent + upcoming earnings."""
    profile = (_get("profile", symbol=symbol) or [{}])[0]
    ratios = (_get("ratios-ttm", symbol=symbol) or [{}])[0]
    # Free tier caps limit at 5; that easily covers recent + next earnings.
    earnings = _get("earnings", symbol=symbol, limit=5) or []
    recent, upcoming = split_earnings(earnings, date.today())

    return {
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "market_cap": profile.get("marketCap"),
        "beta": profile.get("beta"),
        "pe_ttm": ratios.get("priceToEarningsRatioTTM"),
        "peg_ttm": ratios.get("priceToEarningsGrowthRatioTTM"),
        "price_to_sales_ttm": ratios.get("priceToSalesRatioTTM"),
        "debt_to_equity": ratios.get("debtToEquityRatioTTM"),
        "recent_earnings": recent[:4],
        "next_earnings_date": upcoming[0]["date"] if upcoming else None,
        "days_to_next_earnings": (
            (date.fromisoformat(upcoming[0]["date"]) - date.today()).days if upcoming else None),
        # Context flag: did this company report within the last 5 days? A
        # trigger right after a report usually IS the report's aftermath.
        "just_reported": _just_reported(recent, date.today()),
    }


def _bundled_sp500() -> list[str]:
    """The bundled point-in-time S&P 500 snapshot (free-tier fallback)."""
    from importlib.resources import files

    text = (files("smartcapital") / "data" / "sp500.txt").read_text()
    symbols = {
        line.strip() for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not symbols:
        raise RuntimeError("bundled S&P 500 snapshot is empty")
    return sorted(symbols)


def sp500_symbols(live: bool = False, cache_days: int = 7, cache_dir: str = ".cache") -> list[str]:
    """S&P 500 constituents.

    The index membership list changes only a handful of times a year and FMP's
    live `sp500-constituent` endpoint is paid-only, so by default we just return
    the bundled snapshot (refresh it manually a few times a year). Pass
    `live=True` only if your FMP tier includes that endpoint - it is then fetched
    and cached on disk, falling back to the bundle if the tier still can't reach
    it (401/402/403) or it comes back empty.
    """
    if not live:
        return _bundled_sp500()

    import time
    from pathlib import Path

    cache = Path(cache_dir) / "sp500.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < cache_days * 86400:
        return json.loads(cache.read_text())
    try:
        rows = _get("sp500-constituent") or []
        symbols = sorted({r["symbol"] for r in rows if r.get("symbol")})
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (401, 402, 403):
            raise
        symbols = _bundled_sp500()  # tier can't reach the paid endpoint
    if not symbols:
        symbols = _bundled_sp500()
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(symbols))
    return symbols


def news(symbol: str, limit: int = 8) -> list[dict]:
    """Recent headlines for the symbol: date, title, source. Text only -
    the LLM weighs them; nothing here triggers anything. Returns [] when the
    account tier can't reach the (paid) news endpoint, so the pipeline degrades
    gracefully rather than failing the whole analysis."""
    try:
        rows = _get("news/stock", symbols=symbol, limit=limit) or []
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 402, 403):
            return []
        raise
    return [
        {"date": r.get("publishedDate"), "title": r.get("title"), "source": r.get("site")}
        for r in rows
    ]


def split_earnings(rows: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """FMP's earnings feed mixes past reports (epsActual set) and scheduled
    future dates (epsActual null). Split into (recent desc, upcoming asc)."""
    recent, upcoming = [], []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        entry = {"date": d, "eps_actual": r.get("epsActual"), "eps_estimate": r.get("epsEstimated")}
        if date.fromisoformat(d) > today or r.get("epsActual") is None:
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
