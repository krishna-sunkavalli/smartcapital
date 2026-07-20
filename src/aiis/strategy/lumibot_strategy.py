"""Lumibot adapter. Lumibot supplies scheduling and (optionally) backtesting
of the TRIGGER LAYER ONLY - the LLM's decisions are explicitly not
backtestable (the model knows historical outcomes), so backtests here score
trigger quality, never LLM judgment.

Requires the optional `lumibot` extra: pip install "aiis[lumibot]".
"""
from __future__ import annotations

import logging

from lumibot.strategies import Strategy

from aiis.config import load_config
from aiis.db.session import get_session, init_db
from aiis.strategy.engine import Engine
from aiis.triggers.buy_triggers import detect_buy_triggers

log = logging.getLogger(__name__)


class AiisStrategy(Strategy):
    """Live/paper mode: delegates each polling iteration to the Engine.
    Nothing in here trades directly - Lumibot is scheduling only; execution
    goes through the approval + guardrail pipeline."""

    parameters = {"watchlist": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "CRM"]}

    def initialize(self) -> None:
        cfg = load_config()
        self.sleeptime = f"{cfg.triggers.poll_interval_minutes}M"
        init_db()
        self.engine = Engine(cfg, self.parameters["watchlist"])

    def on_trading_iteration(self) -> None:
        with get_session() as session:
            proposals = self.engine.scan_buy_side(session)
            proposals += self.engine.review_positions(session)
        for p in proposals:
            log.info("proposal %s %s awaiting human approval", p.action, p.symbol)


class TriggerBacktest(Strategy):
    """Backtest mode: counts trigger firings only. Used to tune trigger
    parameters against historical data; produces no orders and calls no LLM."""

    parameters = {"watchlist": ["AAPL", "MSFT", "NVDA"], "hits": []}

    def initialize(self) -> None:
        self.sleeptime = "1D"
        self.cfg = load_config()

    def on_trading_iteration(self) -> None:
        for symbol in self.parameters["watchlist"]:
            bars = self.get_historical_prices(symbol, 320, "day")
            if bars is None:
                continue
            for trig in detect_buy_triggers(bars.df, self.cfg.triggers.buy):
                self.parameters["hits"].append(
                    {"dt": self.get_datetime().isoformat(), "symbol": symbol,
                     "trigger": trig.trigger_type, **trig.details})
