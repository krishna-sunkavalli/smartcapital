"""Pure technical indicator math over daily OHLCV frames. Deterministic code
owns the math - none of this is delegated to the LLM.
"""
from __future__ import annotations

import pandas as pd


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    # 100*g/(g+l) == 100 - 100/(1+g/l), but well-defined when losses are zero.
    total = gain + loss
    return (100 * gain / total.where(total > 0)).fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def crossed_above(a: pd.Series, b: pd.Series) -> bool:
    """True if `a` crossed above `b` on the most recent bar."""
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] <= b.iloc[-2] and a.iloc[-1] > b.iloc[-1])


def crossed_below(a: pd.Series, b: pd.Series) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] >= b.iloc[-2] and a.iloc[-1] < b.iloc[-1])


def indicator_snapshot(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> dict:
    """The numeric snapshot injected into the analysis packet."""
    close = df["close"]
    m = macd(close)
    return {
        "close": float(close.iloc[-1]),
        "ema20": float(ema(close, fast).iloc[-1]),
        "ema50": float(ema(close, slow).iloc[-1]),
        "ema200": float(ema(close, 200).iloc[-1]) if len(close) >= 200 else None,
        "rsi14": float(rsi(close).iloc[-1]),
        "macd": float(m["macd"].iloc[-1]),
        "macd_signal": float(m["signal"].iloc[-1]),
        "high_20d": float(close.tail(20).max()),
        "avg_volume_20d": float(df["volume"].tail(20).mean()),
        "last_volume": float(df["volume"].iloc[-1]),
        "return_1m_pct": float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else None,
        "return_3m_pct": float((close.iloc[-1] / close.iloc[-63] - 1) * 100) if len(close) > 63 else None,
    }
