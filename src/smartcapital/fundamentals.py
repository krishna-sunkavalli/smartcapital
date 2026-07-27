"""Fundamentals + earnings + news from Yahoo Finance (via yfinance). Keyless and
covers the whole S&P 500 - FMP's free tier gated most symbols behind 402s. The
LLM only ever sees data fetched here; it is never allowed to supply facts from
memory.

yfinance scrapes Yahoo, so calls can be throttled (especially from datacenter
IPs). Every network path degrades gracefully to partial/empty data and results
are cached briefly, so a throttle yields a thinner packet - which the analyst
treats as a risk - rather than failing the whole analysis.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from threading import Lock

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# A short in-process cache smooths over Yahoo throttling and avoids refetching
# the same symbol within a scan cycle. Keyed by (kind, symbol).
_CACHE_TTL_SECONDS = 600
_cache: dict[tuple[str, str], tuple[float, object]] = {}
_cache_lock = Lock()


def _cache_get(kind: str, symbol: str):
    with _cache_lock:
        hit = _cache.get((kind, symbol))
    if hit and (time.time() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _cache_put(kind: str, symbol: str, value) -> None:
    with _cache_lock:
        _cache[(kind, symbol)] = (time.time(), value)


def _num(v):
    """Coerce a yfinance cell to float or None (NaN/missing -> None)."""
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_info(symbol: str) -> dict:
    try:
        return yf.Ticker(symbol).info or {}
    except Exception as e:  # yfinance raises assorted errors on throttle/parse
        log.warning("yfinance info failed for %s (%s); continuing without it", symbol, e)
        return {}


def _earnings(symbol: str) -> tuple[list[dict], list[dict]]:
    """Recent (desc) and upcoming (asc) earnings rows from yfinance."""
    try:
        df = yf.Ticker(symbol).get_earnings_dates(limit=12)
    except Exception as e:
        log.warning("yfinance earnings failed for %s (%s); continuing without it", symbol, e)
        return [], []
    if df is None or df.empty:
        return [], []
    rows = [
        {"date": ts.date().isoformat(),
         "eps_actual": _num(row.get("Reported EPS")),
         "eps_estimate": _num(row.get("EPS Estimate"))}
        for ts, row in df.iterrows()
    ]
    return split_earnings(rows, date.today())


def snapshot(symbol: str) -> dict:
    """One compact dict: profile, valuation, recent + upcoming earnings.

    Missing fields (unknown, or the provider was throttled) come back as None;
    the analyst is instructed to treat missing data as a risk.
    """
    cached = _cache_get("snap", symbol)
    if cached is not None:
        return cached
    info = _safe_info(symbol)
    recent, upcoming = _earnings(symbol)

    snap = {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta"),
        "pe_ttm": info.get("trailingPE"),
        "peg_ttm": info.get("trailingPegRatio"),
        "price_to_sales_ttm": info.get("priceToSalesTrailing12Months"),
        # yfinance reports debt/equity as a percentage (e.g. 35.3); normalize to
        # a ratio to match the analyst's expectation (e.g. 0.353).
        "debt_to_equity": (info.get("debtToEquity") / 100
                           if info.get("debtToEquity") is not None else None),
        "recent_earnings": recent[:4],
        "next_earnings_date": upcoming[0]["date"] if upcoming else None,
        "days_to_next_earnings": (
            (date.fromisoformat(upcoming[0]["date"]) - date.today()).days if upcoming else None),
        # Context flag: did this company report within the last 5 days? A
        # trigger right after a report usually IS the report's aftermath.
        "just_reported": _just_reported(recent, date.today()),
    }
    _cache_put("snap", symbol, snap)
    return snap


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


def sp500_symbols(cache_days: int = 7, cache_dir: str = ".cache") -> list[str]:
    """S&P 500 constituents from the bundled point-in-time snapshot.

    Index membership changes only a handful of times a year, so a bundled list
    (refreshed manually) keeps the universe deterministic and free of an extra
    live dependency. ``cache_days``/``cache_dir`` are accepted for call-site
    compatibility but unused - the bundle needs no cache.
    """
    return _bundled_sp500()


def news(symbol: str, limit: int = 8) -> list[dict]:
    """Recent headlines for the symbol: date, title, source. Text only - the
    LLM weighs them; nothing here triggers anything, and all headline text is
    treated as untrusted data. Degrades to [] on any provider error."""
    cached = _cache_get("news", symbol)
    if cached is not None:
        return cached
    try:
        raw = yf.Ticker(symbol).news or []
    except Exception as e:
        log.warning("yfinance news failed for %s (%s); continuing without it", symbol, e)
        raw = []
    out: list[dict] = []
    for item in raw[:limit]:
        c = item.get("content", item)  # yfinance nests the article under 'content'
        title = c.get("title")
        if not title:
            continue
        provider = c.get("provider")
        source = provider.get("displayName") if isinstance(provider, dict) else provider
        out.append({"date": _news_date(c.get("pubDate")), "title": title, "source": source})
    _cache_put("news", symbol, out)
    return out


def _news_date(pub) -> str | None:
    """yfinance pubDate is an ISO-8601 string (e.g. '2026-07-27T15:03:16Z');
    reduce it to a plain date, tolerating epoch ints or missing values."""
    if pub is None:
        return None
    if isinstance(pub, (int, float)):
        return datetime.fromtimestamp(pub, tz=timezone.utc).date().isoformat()
    try:
        return datetime.fromisoformat(str(pub).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def split_earnings(rows: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """Split normalized earnings rows into (recent desc, upcoming asc).

    Rows use ``{date, eps_actual, eps_estimate}``. A date today-or-later is
    treated as upcoming (a scheduled report, ``eps_actual`` still None);
    anything earlier is a past report.
    """
    recent, upcoming = [], []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        entry = {"date": d, "eps_actual": r.get("eps_actual"),
                 "eps_estimate": r.get("eps_estimate")}
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
