"""Fundamentals feed via Financial Modeling Prep (dedicated provider - Alpaca
has no fundamentals, and the LLM is never allowed to supply them from memory).

Provides: earnings history, guidance/analyst estimates, valuation ratios,
sector classification, and the S&P 500 constituent list used by the universe
guardrail.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aiis.config import secrets
from aiis.data.base import DataPoint, MissingDataError

SOURCE = "fmp"
BASE = "https://financialmodelingprep.com/api/v3"


class Fundamentals:
    def __init__(self, cache_dir: str | Path = ".cache") -> None:
        self._key = secrets().fmp_api_key
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(exist_ok=True)

    def _get(self, path: str, **params) -> list | dict:
        params["apikey"] = self._key
        r = httpx.get(f"{BASE}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # --- universe -----------------------------------------------------------
    def sp500_constituents(self, max_age_days: int = 7) -> set[str]:
        cache = self._cache_dir / "sp500.json"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_days * 86400:
            return set(json.loads(cache.read_text()))
        rows = self._get("sp500_constituent")
        symbols = {row["symbol"] for row in rows}
        if not symbols:
            raise MissingDataError("S&P 500 constituent list came back empty")
        cache.write_text(json.dumps(sorted(symbols)))
        return symbols

    # --- per-symbol fundamentals -------------------------------------------
    def profile(self, symbol: str) -> DataPoint:
        rows = self._get(f"profile/{symbol}")
        if not rows:
            return DataPoint(value=None, source=SOURCE, as_of=datetime.now(timezone.utc))
        p = rows[0]
        return DataPoint(
            value={
                "sector": p.get("sector"),
                "industry": p.get("industry"),
                "market_cap": p.get("mktCap"),
                "beta": p.get("beta"),
            },
            source=SOURCE,
            as_of=datetime.now(timezone.utc),
        )

    def ratios(self, symbol: str) -> DataPoint:
        rows = self._get(f"ratios-ttm/{symbol}")
        value = None
        if rows:
            r = rows[0]
            value = {
                "pe_ttm": r.get("peRatioTTM"),
                "peg_ttm": r.get("pegRatioTTM"),
                "ps_ttm": r.get("priceToSalesRatioTTM"),
                "debt_to_equity": r.get("debtEquityRatioTTM"),
                "gross_margin_ttm": r.get("grossProfitMarginTTM"),
            }
        return DataPoint(value=value, source=SOURCE, as_of=datetime.now(timezone.utc))

    def earnings_history(self, symbol: str, quarters: int = 8) -> DataPoint:
        rows = self._get(f"historical/earning_calendar/{symbol}", limit=quarters)
        value = [
            {
                "date": r.get("date"),
                "eps_actual": r.get("eps"),
                "eps_estimate": r.get("epsEstimated"),
                "revenue_actual": r.get("revenue"),
                "revenue_estimate": r.get("revenueEstimated"),
            }
            for r in rows or []
        ] or None
        return DataPoint(value=value, source=SOURCE, as_of=datetime.now(timezone.utc))

    def analyst_estimates(self, symbol: str) -> DataPoint:
        rows = self._get(f"analyst-estimates/{symbol}", limit=2)
        value = rows[:2] if rows else None
        return DataPoint(value=value, source=SOURCE, as_of=datetime.now(timezone.utc))
