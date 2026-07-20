"""Buy-side triggers (v2). Triggers initiate analysis; they never make the
decision. Deferred to v2.1: unusual options activity, IV signals, unstructured
news and macro headlines.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aiis.config import BuyTriggerCfg
from aiis.triggers import indicators as ind


@dataclass
class Trigger:
    trigger_type: str
    side: str
    details: dict


def detect_buy_triggers(df: pd.DataFrame, cfg: BuyTriggerCfg) -> list[Trigger]:
    """Evaluate all buy-side trigger conditions on a daily OHLCV frame.
    Returns raw detections; dedup/cooldown and the earnings blackout are
    applied downstream before any LLM invocation."""
    out: list[Trigger] = []
    close = df["close"]
    if len(close) < 60:
        return out
    price = float(close.iloc[-1])

    # 1. Price approaching or crossing EMA-200
    if len(close) >= 200:
        ema200 = ind.ema(close, 200)
        prox = abs(price / float(ema200.iloc[-1]) - 1)
        if prox <= cfg.ema200_proximity_pct or ind.crossed_above(close, ema200):
            out.append(Trigger("ema200_touch", "buy", {"price": price, "ema200": float(ema200.iloc[-1]),
                                                       "proximity_pct": round(prox * 100, 2)}))

    # 2. EMA-20/50 crossover
    fast, slow = ind.ema(close, cfg.ema_fast), ind.ema(close, cfg.ema_slow)
    if ind.crossed_above(fast, slow):
        out.append(Trigger("ema_20_50_cross", "buy",
                           {"ema_fast": float(fast.iloc[-1]), "ema_slow": float(slow.iloc[-1])}))

    # 3. RSI / MACD condition
    rsi_now = float(ind.rsi(close).iloc[-1])
    m = ind.macd(close)
    if rsi_now <= cfg.rsi_oversold:
        out.append(Trigger("rsi_oversold", "buy", {"rsi14": round(rsi_now, 1)}))
    if ind.crossed_above(m["macd"], m["signal"]) and float(m["macd"].iloc[-1]) < 0:
        out.append(Trigger("macd_bull_cross", "buy",
                           {"macd": float(m["macd"].iloc[-1]), "signal": float(m["signal"].iloc[-1])}))

    # 4. Significant pullback with volume confirmation
    high20 = float(close.tail(20).max())
    pullback = 1 - price / high20
    vol_mult = float(df["volume"].iloc[-1]) / max(float(df["volume"].tail(20).mean()), 1.0)
    if pullback >= cfg.pullback_pct and vol_mult >= cfg.pullback_volume_mult:
        out.append(Trigger("pullback_volume", "buy",
                           {"pullback_pct": round(pullback * 100, 2), "volume_mult": round(vol_mult, 2)}))
    return out
