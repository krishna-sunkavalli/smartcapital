"""Structured events feed: earnings calendar and SEC filings ONLY.

Unstructured news and macro headlines are explicitly excluded from v2 triggers
(deferred to v2.1) - they are noisy and hard to detect reliably.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from aiis.config import secrets
from aiis.data.base import DataPoint

FMP_BASE = "https://financialmodelingprep.com/api/v3"
SEC_BASE = "https://data.sec.gov"


class StructuredEvents:
    def __init__(self) -> None:
        self._key = secrets().fmp_api_key

    def next_earnings_date(self, symbol: str) -> DataPoint:
        r = httpx.get(
            f"{FMP_BASE}/earning_calendar",
            params={
                "from": date.today().isoformat(),
                "to": (date.today() + timedelta(days=120)).isoformat(),
                "apikey": self._key,
            },
            timeout=30,
        )
        r.raise_for_status()
        dates = sorted(row["date"] for row in r.json() if row.get("symbol") == symbol)
        return DataPoint(
            value=dates[0] if dates else None,
            source="fmp-earnings-calendar",
            as_of=datetime.now(timezone.utc),
        )

    def earnings_released_since(self, symbol: str, since: datetime) -> bool:
        """True if this held name reported earnings since `since` (review trigger)."""
        r = httpx.get(
            f"{FMP_BASE}/historical/earning_calendar/{symbol}",
            params={"limit": 4, "apikey": self._key},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json() or []:
            if row.get("eps") is None:
                continue  # not yet reported
            reported = datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc)
            if reported >= since:
                return True
        return False

    def recent_filings(self, cik: str, forms: tuple[str, ...] = ("8-K", "10-Q", "10-K")) -> DataPoint:
        """Recent SEC filings for a company (by CIK), structured metadata only."""
        r = httpx.get(
            f"{SEC_BASE}/submissions/CIK{int(cik):010d}.json",
            headers={"User-Agent": "aiis/2.0 (personal research)"},
            timeout=30,
        )
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
        out = [
            {"form": f, "filed": d, "accession": a}
            for f, d, a in zip(
                recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", [])
            )
            if f in forms
        ][:10]
        return DataPoint(value=out or None, source="sec-edgar", as_of=datetime.now(timezone.utc))
