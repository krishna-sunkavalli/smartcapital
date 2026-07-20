"""Fundamentals snapshot from Financial Modeling Prep. The LLM only ever sees
data fetched here - it is never allowed to supply fundamentals from memory."""
from __future__ import annotations

import httpx

from smartcapital.config import secrets

BASE = "https://financialmodelingprep.com/api/v3"


def snapshot(symbol: str) -> dict:
    """One compact dict: profile, valuation, recent earnings."""
    key = secrets().fmp_api_key

    def get(path: str, **params):
        params["apikey"] = key
        r = httpx.get(f"{BASE}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    profile = (get(f"profile/{symbol}") or [{}])[0]
    ratios = (get(f"ratios-ttm/{symbol}") or [{}])[0]
    earnings = get(f"historical/earning_calendar/{symbol}", limit=4) or []

    return {
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "market_cap": profile.get("mktCap"),
        "beta": profile.get("beta"),
        "pe_ttm": ratios.get("peRatioTTM"),
        "peg_ttm": ratios.get("pegRatioTTM"),
        "price_to_sales_ttm": ratios.get("priceToSalesRatioTTM"),
        "debt_to_equity": ratios.get("debtEquityRatioTTM"),
        "recent_earnings": [
            {"date": e.get("date"), "eps_actual": e.get("eps"), "eps_estimate": e.get("epsEstimated")}
            for e in earnings
        ],
    }
