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


class ScanCfg(BaseModel):
    # Throttles for large universes: the human gate (you) is the scarce
    # resource, so triggers are ranked by severity and capped.
    max_analyses_per_cycle: int = 3
    max_analyses_per_day: int = 6
    universe_cache_days: int = 7


class OrderCfg(BaseModel):
    notional_usd: float = 500.0
    price_band_pct: float = 0.01
    min_cash_buffer_usd: float = 500.0


class LlmCfg(BaseModel):
    model: str = "claude-opus-4-8"
    max_tokens: int = 8000          # covers internal thinking + the JSON verdict
    effort: str = "high"            # low | medium | high | xhigh | max


class ApprovalCfg(BaseModel):
    ttl_minutes: int = 60


class Config(BaseModel):
    # "sp500" scans the full S&P 500 (list fetched from FMP, cached);
    # or provide an explicit ticker list.
    watchlist: str | list[str] = "sp500"
    triggers: TriggersCfg = TriggersCfg()
    scan: ScanCfg = ScanCfg()
    order: OrderCfg = OrderCfg()
    llm: LlmCfg = LlmCfg()
    approval: ApprovalCfg = ApprovalCfg()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    # No default on purpose: set ALPACA_ENV to "paper" or "live" explicitly.
    alpaca_env: str = ""
    fmp_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def load_config(path: str | Path | None = None) -> Config:
    import os
    p = Path(path or os.environ.get("SMARTCAPITAL_CONFIG", "config.yaml"))
    if not p.exists():
        p = Path("config.example.yaml")
    if p.exists():
        with open(p) as f:
            return Config.model_validate(yaml.safe_load(f) or {})
    return Config()


@lru_cache
def secrets() -> Secrets:
    return Secrets()
