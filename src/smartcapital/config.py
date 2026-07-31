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
    # resource, so triggers are ranked by size-weighted severity and capped.
    max_analyses_per_cycle: int = 3
    max_analyses_per_day: int = 12
    universe_cache_days: int = 7


class OrderCfg(BaseModel):
    notional_usd: float = 500.0
    price_band_pct: float = 0.01
    min_cash_buffer_usd: float = 500.0


class LlmCfg(BaseModel):
    # Azure AI Foundry: the analyst is a prompt agent backed by an Azure OpenAI
    # model deployment, reached through the project endpoint with Entra auth (no key).
    project_endpoint: str = ""   # https://<resource>.services.ai.azure.com/api/projects/<project>
    agent_name: str = "smartcapital-analyst"
    model: str = "gpt-5-mini"  # the Azure OpenAI *deployment name* in the Foundry project
    max_tokens: int = 8000          # covers internal thinking + the JSON verdict
    # When true, the analyst returns a deterministic stub instead of calling
    # Foundry - lets the whole pipeline run locally with no Azure. Set via config
    # or the SMARTCAPITAL_LLM_DRY_RUN env var.
    dry_run: bool = False


class ApprovalCfg(BaseModel):
    ttl_minutes: int = 60
    # Max age of an approval before execution; a stale approval (e.g. granted
    # just before close, unfilled into the next session) is voided, not placed.
    execute_ttl_minutes: int = 120


class Config(BaseModel):
    # "sp500" or "nasdaq100" scan a full index (bundled point-in-time
    # snapshot); or provide an explicit ticker list.
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
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def load_config(path: str | Path | None = None) -> Config:
    import os
    p = Path(path or os.environ.get("SMARTCAPITAL_CONFIG", "config.yaml"))
    if not p.exists():
        p = Path("config.example.yaml")
    if p.exists():
        with open(p) as f:
            cfg = Config.model_validate(yaml.safe_load(f) or {})
    else:
        cfg = Config()
    # The Foundry endpoint is injected by the Azure deploy; env wins when the
    # config file leaves it blank so the same image runs anywhere.
    if not cfg.llm.project_endpoint:
        cfg.llm.project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    # Env override for offline local testing (Path A): no Foundry call.
    if os.environ.get("SMARTCAPITAL_LLM_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on"):
        cfg.llm.dry_run = True
    return cfg


@lru_cache
def secrets() -> Secrets:
    return Secrets()
