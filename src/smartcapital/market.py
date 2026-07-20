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
        return bars.xs(symbol) if isinstance(bars.index, pd.MultiIndex) else bars

    def market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def cash(self) -> float:
        return float(self.trading.get_account().cash)
