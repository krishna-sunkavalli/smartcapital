"""v1 triggers - deterministic, thresholds from config, pure functions:

1. down_day:     price down >= N% vs previous daily close
2. below_ema200: price below the 200-day EMA

Cooldown (persisted in db.py) makes each (symbol, trigger) fire once per
window instead of every polling cycle.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smartcapital.config import TriggersCfg


@dataclass
class Trigger:
    trigger_type: str
    details: dict


def ta_snapshot(df: pd.DataFrame, latest_price: float) -> dict:
    """The technical picture handed to the LLM, computed from raw bars."""
    close = df["close"]
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close) >= 200 else None
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
    prev_close = float(close.iloc[-1])
    return {
        "price": latest_price,
        "prev_close": prev_close,
        "day_change_pct": round((latest_price / prev_close - 1) * 100, 2),
        "ema50": round(ema50, 2) if ema50 else None,
        "ema200": round(ema200, 2) if ema200 else None,
        "pct_vs_ema200": round((latest_price / ema200 - 1) * 100, 2) if ema200 else None,
        "high_52w": round(float(close.tail(252).max()), 2),
        "pct_off_52w_high": round((latest_price / float(close.tail(252).max()) - 1) * 100, 2),
        "avg_volume_20d": int(df["volume"].tail(20).mean()),
        "return_1m_pct": round((prev_close / float(close.iloc[-21]) - 1) * 100, 2) if len(close) > 21 else None,
        "return_3m_pct": round((prev_close / float(close.iloc[-63]) - 1) * 100, 2) if len(close) > 63 else None,
    }


def detect(df: pd.DataFrame, latest_price: float, cfg: TriggersCfg) -> list[Trigger]:
    """df = daily bars up to and including the previous session."""
    out: list[Trigger] = []
    if df is None or len(df) < 60:
        return out
    close = df["close"]
    prev_close = float(close.iloc[-1])

    # 1. Down >= N% on the day
    change = latest_price / prev_close - 1
    if change <= -cfg.down_day_pct:
        out.append(Trigger("down_day", {"day_change_pct": round(change * 100, 2)}))

    # 2. Below the 200-day EMA
    if len(close) >= 200:
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        if latest_price < ema200:
            out.append(Trigger("below_ema200", {
                "price": latest_price, "ema200": round(ema200, 2),
                "pct_below": round((1 - latest_price / ema200) * 100, 2)}))
    return out
