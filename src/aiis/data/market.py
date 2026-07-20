"""Market data feed (Alpaca): historical/real-time prices, corporate actions,
clock. Order execution lives in execution/, not here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

from aiis.config import secrets
from aiis.data.base import DataPoint

SOURCE = "alpaca"


class MarketData:
    def __init__(self) -> None:
        s = secrets()
        self._data = StockHistoricalDataClient(s.alpaca_api_key, s.alpaca_secret_key)
        self._trading = TradingClient(s.alpaca_api_key, s.alpaca_secret_key, paper=s.alpaca_env != "live")

    def latest_price(self, symbol: str) -> DataPoint:
        trade = self._data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))[symbol]
        return DataPoint(value=float(trade.price), source=SOURCE, as_of=trade.timestamp)

    def daily_bars(self, symbol: str, days: int = 320) -> tuple[pd.DataFrame, DataPoint]:
        """Daily OHLCV frame plus a DataPoint stamped with the last bar time,
        so freshness of the bar history itself is checkable."""
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6))
        bars = self._data.get_stock_bars(
            StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start)
        ).df
        if bars.empty:
            return bars, DataPoint(value=None, source=SOURCE, as_of=datetime.now(timezone.utc))
        df = bars.xs(symbol) if isinstance(bars.index, pd.MultiIndex) else bars
        last_ts = df.index[-1].to_pydatetime()
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        return df, DataPoint(value=len(df), source=SOURCE, as_of=last_ts)

    def clock(self) -> dict:
        c = self._trading.get_clock()
        return {
            "is_open": c.is_open,
            "next_open": c.next_open,
            "next_close": c.next_close,
            "now": c.timestamp,
        }

    def account(self) -> dict:
        a = self._trading.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "trading_blocked": bool(a.trading_blocked),
        }
