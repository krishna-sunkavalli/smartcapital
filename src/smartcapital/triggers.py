"""v1 triggers - deterministic, thresholds from config, pure functions:

1. down_day:         price down >= N% vs previous daily close
2. ema200_cross_down: price crossing DOWN through the 200-day EMA - an event
   (yesterday closed at/above it, live price is below it), not a state. At
   S&P-500 scale ~a third of the index is *below* its EMA-200 at any time, so
   a state trigger would flood the pipeline; a crossing fires once.

Each trigger carries a `severity` used to rank candidates when more triggers
fire in one cycle than the analysis caps allow. Cooldown (in state.py) makes
each (symbol, trigger) fire once per window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from smartcapital.config import TriggersCfg


@dataclass
class Trigger:
    trigger_type: str
    details: dict
    severity: float = 0.0  # bigger = more extreme = analyzed first


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

    # 1. Down >= N% on the day (severity = how far past the threshold)
    change = latest_price / prev_close - 1
    if change <= -cfg.down_day_pct:
        out.append(Trigger("down_day",
                           {"day_change_pct": round(change * 100, 2)},
                           severity=abs(change)))

    # 2. Crossing DOWN through the 200-day EMA
    if len(close) >= 200:
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        if prev_close >= ema200 and latest_price < ema200:
            pct_below = 1 - latest_price / ema200
            out.append(Trigger("ema200_cross_down", {
                "price": latest_price, "ema200": round(ema200, 2),
                "pct_below": round(pct_below * 100, 2)},
                severity=pct_below))
    return out
