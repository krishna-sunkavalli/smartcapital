"""Typed configuration: YAML limits file + .env secrets.

Everything under these models is a deterministic guardrail parameter. Nothing
here is ever exposed to the LLM for modification.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RolloutCfg(BaseModel):
    phase: int = Field(1, ge=1, le=3)
    phase2_max_order_notional: float = 500.0
    phase2_max_daily_approvals: int = 2


class UniverseCfg(BaseModel):
    sp500_only: bool = True
    constituents_cache_days: int = 7
    long_only: bool = True


class ExposureCfg(BaseModel):
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_correlated_tech_pct: float = 0.45
    managed_capital_pct: float = 0.20
    max_daily_deployment: float = 2000.0
    max_weekly_deployment: float = 6000.0
    min_cash_buffer: float = 500.0


class OrdersCfg(BaseModel):
    limit_only: bool = True
    price_band_pct: float = 0.01
    auction_buffer_minutes: int = 10
    order_time_in_force: str = "day"


class BuyTriggerCfg(BaseModel):
    ema200_proximity_pct: float = 0.02
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_oversold: float = 32.0
    pullback_pct: float = 0.07
    pullback_volume_mult: float = 1.5


class TriggersCfg(BaseModel):
    poll_interval_minutes: int = 15
    cooldown_days_per_symbol_trigger: int = 5
    earnings_blackout_trading_days: int = 5
    weekly_review_day: str = "friday"
    drawdown_review_pct: float = 0.12
    concentration_review_pct: float = 0.12
    buy: BuyTriggerCfg = BuyTriggerCfg()


class AnalystCfg(BaseModel):
    model: str = "claude-sonnet-5"
    prompt_version: str = "2.0.0"
    temperature: float = 0.0
    samples: int = 1
    max_tokens: int = 2000
    buy_rate_alert_threshold: float = 0.35
    buy_rate_window: int = 50


class FreshnessCfg(BaseModel):
    market_price_seconds: int = 900
    market_daily_bars_hours: int = 30
    fundamentals_days: int = 7
    earnings_calendar_days: int = 2
    feed_disagreement_pct: float = 0.03


class ApprovalCfg(BaseModel):
    ttl_minutes: int = 60
    daily_approval_cap: int = 3
    min_read_delay_seconds: int = 45
    typeback_notional_threshold: float = 1000.0
    weekly_digest_day: str = "sunday"
    approval_rate_alarm: float = 0.90


class SafetyCfg(BaseModel):
    max_proposals_per_rolling_hour: int = 4
    max_proposals_per_rolling_day: int = 8


class AppConfig(BaseModel):
    rollout: RolloutCfg = RolloutCfg()
    universe: UniverseCfg = UniverseCfg()
    exposure: ExposureCfg = ExposureCfg()
    orders: OrdersCfg = OrdersCfg()
    triggers: TriggersCfg = TriggersCfg()
    analyst: AnalystCfg = AnalystCfg()
    freshness: FreshnessCfg = FreshnessCfg()
    approval: ApprovalCfg = ApprovalCfg()
    safety: SafetyCfg = SafetyCfg()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_env: str = "paper"
    fmp_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_allowed_chat_id: str = ""
    approval_signing_secret: str = ""
    aiis_database_url: str = "sqlite:///aiis.db"


def load_config(path: str | Path = "config/config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        example = p.with_name("config.example.yaml")
        if example.exists():
            p = example
        else:
            return AppConfig()
    with open(p) as f:
        return AppConfig.model_validate(yaml.safe_load(f) or {})


@lru_cache
def secrets() -> Secrets:
    return Secrets()
