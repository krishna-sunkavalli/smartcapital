from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class TriggersCfg(BaseModel):
    poll_interval_minutes: int = 15
    down_day_pct: float = 0.05
    cooldown_days: int = 5


class OrderCfg(BaseModel):
    notional_usd: float = 500.0
    price_band_pct: float = 0.01
    min_cash_buffer_usd: float = 500.0


class LlmCfg(BaseModel):
    model: str = "claude-sonnet-5"
    max_tokens: int = 1500
    temperature: float = 0.0


class ApprovalCfg(BaseModel):
    ttl_minutes: int = 60


class Config(BaseModel):
    watchlist: list[str] = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "CRM"]
    triggers: TriggersCfg = TriggersCfg()
    order: OrderCfg = OrderCfg()
    llm: LlmCfg = LlmCfg()
    approval: ApprovalCfg = ApprovalCfg()


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
    database_url: str = "sqlite:///smartcapital.db"


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        p = Path("config.example.yaml")
    if p.exists():
        with open(p) as f:
            return Config.model_validate(yaml.safe_load(f) or {})
    return Config()


@lru_cache
def secrets() -> Secrets:
    return Secrets()
