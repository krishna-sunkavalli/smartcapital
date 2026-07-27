"""Alpaca: raw bars, latest price, clock, account. Indicators are computed
locally from bars (Alpaca has no indicator endpoints)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

from smartcapital.config import secrets


def _completed_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Drop today's still-forming daily bar so callers see only closed sessions.

    While the market is open Alpaca returns a partial bar for the current
    session as the last row. Leaving it in makes ``close.iloc[-1]`` (used as the
    previous close by triggers) equal to today's live price, which collapses the
    day-change to ~0% and hides real intraday moves (e.g. a -5% day). Triggers
    require the *previous* session close, so exclude the current UTC date.
    """
    if df is None or df.empty:
        return df
    today = datetime.now(timezone.utc).date()
    return df[df.index.date < today]


class Market:
    def __init__(self) -> None:
        s = secrets()
        if s.alpaca_env not in ("paper", "live"):
            raise RuntimeError("Set ALPACA_ENV to 'paper' or 'live' explicitly in .env")
        self.data = StockHistoricalDataClient(s.alpaca_api_key, s.alpaca_secret_key)
        self.trading = TradingClient(s.alpaca_api_key, s.alpaca_secret_key,
                                     paper=s.alpaca_env != "live")

    def latest_price(self, symbol: str) -> float:
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        return float(self.data.get_stock_latest_trade(req)[symbol].price)

    def daily_bars(self, symbol: str, days: int = 260) -> pd.DataFrame:
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6))
        bars = self.data.get_stock_bars(
            StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start)).df
        if bars.empty:
            return bars
        df = bars.xs(symbol) if isinstance(bars.index, pd.MultiIndex) else bars
        return _completed_sessions(df)

    # --- batched variants: ~10 API calls for the whole S&P 500 instead of ~1000
    def latest_prices(self, symbols: list[str], chunk: int = 200) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in range(0, len(symbols), chunk):
            trades = self.data.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbols[i:i + chunk]))
            out.update({sym: float(t.price) for sym, t in trades.items()})
        return out

    def daily_bars_multi(self, symbols: list[str], days: int = 260,
                         chunk: int = 50) -> dict[str, pd.DataFrame]:
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6))
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), chunk):
            bars = self.data.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=symbols[i:i + chunk],
                timeframe=TimeFrame.Day, start=start)).df
            if bars.empty:
                continue
            if isinstance(bars.index, pd.MultiIndex):
                for sym in bars.index.get_level_values(0).unique():
                    out[sym] = _completed_sessions(bars.xs(sym))
            else:  # single-symbol chunk comes back flat
                out[symbols[i]] = _completed_sessions(bars)
        return out

    def market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def cash(self) -> float:
        return float(self.trading.get_account().cash)
