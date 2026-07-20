import numpy as np
import pandas as pd

from smartcapital.config import TriggersCfg
from smartcapital.triggers import detect, ta_snapshot


def make_df(closes):
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame({"close": closes, "open": closes, "high": closes,
                         "low": closes, "volume": [1e6] * len(closes)})


def test_down_day_fires_at_threshold():
    df = make_df([100.0] * 250)
    cfg = TriggersCfg(down_day_pct=0.05)
    assert "down_day" in {t.trigger_type for t in detect(df, 95.0, cfg)}
    assert "down_day" not in {t.trigger_type for t in detect(df, 96.0, cfg)}


def test_below_ema200_fires():
    df = make_df([100.0] * 250)  # EMA-200 of a constant series is 100
    cfg = TriggersCfg()
    assert "below_ema200" in {t.trigger_type for t in detect(df, 99.0, cfg)}
    assert "below_ema200" not in {t.trigger_type for t in detect(df, 101.0, cfg)}


def test_no_ema200_trigger_without_enough_history():
    df = make_df([100.0] * 150)
    assert "below_ema200" not in {t.trigger_type for t in detect(df, 90.0, TriggersCfg())}


def test_short_history_no_triggers():
    assert detect(make_df([100.0] * 30), 50.0, TriggersCfg()) == []


def test_ta_snapshot_fields():
    df = make_df(list(np.linspace(90, 110, 260)))
    snap = ta_snapshot(df, 100.0)
    assert snap["price"] == 100.0
    assert snap["ema200"] is not None
    assert snap["pct_off_52w_high"] < 0
    assert isinstance(snap["avg_volume_20d"], int)


def test_cooldown_roundtrip():
    from datetime import timedelta

    from smartcapital.state import Store, utcnow

    store = Store()
    now = utcnow()
    assert not store.in_cooldown("MSFT", "down_day", now)
    store.start_cooldown("MSFT", "down_day", now + timedelta(days=5))
    assert store.in_cooldown("MSFT", "down_day", now)
    assert not store.in_cooldown("MSFT", "down_day", now + timedelta(days=6))
    assert not store.in_cooldown("MSFT", "below_ema200", now)
