import numpy as np
import pandas as pd

from aiis.config import BuyTriggerCfg
from aiis.triggers import indicators as ind
from aiis.triggers.buy_triggers import detect_buy_triggers


def make_df(closes, volumes=None):
    closes = pd.Series(closes, dtype=float)
    volumes = pd.Series(volumes if volumes is not None else [1e6] * len(closes), dtype=float)
    return pd.DataFrame({"close": closes, "open": closes, "high": closes,
                         "low": closes, "volume": volumes})


def test_ema_converges_to_constant():
    s = pd.Series([100.0] * 50)
    assert abs(ind.ema(s, 20).iloc[-1] - 100.0) < 1e-9


def test_rsi_bounds():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert ind.rsi(up).iloc[-1] > 70
    assert ind.rsi(down).iloc[-1] < 30


def test_crossed_above_detects_single_cross():
    a = pd.Series([1.0, 3.0])
    b = pd.Series([2.0, 2.0])
    assert ind.crossed_above(a, b)
    assert not ind.crossed_above(b, a)


def test_pullback_trigger_requires_volume_confirmation():
    cfg = BuyTriggerCfg()
    base = list(np.linspace(100, 130, 80))
    pullback = base + [130 * (1 - 0.10)]  # 10% pullback
    df_low_vol = make_df(pullback)
    df_high_vol = make_df(pullback, volumes=[1e6] * 80 + [2.5e6])
    types_low = {t.trigger_type for t in detect_buy_triggers(df_low_vol, cfg)}
    types_high = {t.trigger_type for t in detect_buy_triggers(df_high_vol, cfg)}
    assert "pullback_volume" not in types_low
    assert "pullback_volume" in types_high


def test_short_history_yields_no_triggers():
    assert detect_buy_triggers(make_df([100.0] * 30), BuyTriggerCfg()) == []
