import numpy as np
import pandas as pd

from smartcapital.config import TriggersCfg
from smartcapital.triggers import detect, ta_snapshot


def make_df(closes):
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame({"close": closes, "open": closes, "high": closes,
                         "low": closes, "volume": [1e6] * len(closes)})


def types(df, price, cfg=None):
    return {t.trigger_type for t in detect(df, price, cfg or TriggersCfg())}


def test_down_day_fires_at_threshold():
    df = make_df([100.0] * 250)
    cfg = TriggersCfg(down_day_pct=0.05)
    assert "down_day" in types(df, 95.0, cfg)
    assert "down_day" not in types(df, 96.0, cfg)


def test_down_day_severity_scales_with_drop():
    df = make_df([100.0] * 250)
    t92 = next(t for t in detect(df, 92.0, TriggersCfg()) if t.trigger_type == "down_day")
    t94 = next(t for t in detect(df, 94.0, TriggersCfg()) if t.trigger_type == "down_day")
    assert t92.severity > t94.severity


def test_ema200_cross_down_fires_on_the_crossing():
    # Closed exactly at the EMA yesterday, live price now below -> crossing
    df = make_df([100.0] * 250)
    assert "ema200_cross_down" in types(df, 99.0)
    assert "ema200_cross_down" not in types(df, 101.0)


def test_ema200_already_below_does_not_refire():
    # A stock in a downtrend: yesterday's close already well below its EMA-200.
    df = make_df(list(np.linspace(150, 100, 250)))
    close, ema = df["close"], df["close"].ewm(span=200, adjust=False).mean()
    assert float(close.iloc[-1]) < float(ema.iloc[-1])  # sanity: state is "below"
    assert "ema200_cross_down" not in types(df, 99.0)   # ...but no crossing event


def test_no_ema200_trigger_without_enough_history():
    df = make_df([100.0] * 150)
    assert "ema200_cross_down" not in types(df, 90.0)


def test_short_history_no_triggers():
    assert detect(make_df([100.0] * 30), 50.0, TriggersCfg()) == []


def test_ta_snapshot_fields():
    df = make_df(list(np.linspace(90, 110, 260)))
    snap = ta_snapshot(df, 100.0)
    assert snap["price"] == 100.0
    assert snap["ema200"] is not None
    assert snap["pct_off_52w_high"] < 0
    assert isinstance(snap["avg_volume_20d"], int)


def test_cooldown_roundtrip(tmp_path):
    from datetime import timedelta

    from smartcapital.state import Store, utcnow

    store = Store(tmp_path / "state.json")
    now = utcnow()
    assert not store.in_cooldown("MSFT", "down_day", now)
    store.start_cooldown("MSFT", "down_day", now + timedelta(days=5))
    assert store.in_cooldown("MSFT", "down_day", now)
    assert not store.in_cooldown("MSFT", "down_day", now + timedelta(days=6))
    assert not store.in_cooldown("MSFT", "ema200_cross_down", now)


def test_daily_analysis_budget(tmp_path):
    from smartcapital.state import Store

    store = Store(tmp_path / "state.json")
    assert store.analyses_today() == 0
    store.record_analysis()
    store.record_analysis()
    assert store.analyses_today() == 2


def test_state_survives_restart(tmp_path):
    from datetime import timedelta

    from smartcapital.state import Store, utcnow

    path = tmp_path / "state.json"
    first = Store(path)
    first.start_cooldown("NVDA", "down_day", utcnow() + timedelta(days=5))
    first.record_analysis()
    first.record_analysis()

    reborn = Store(path)  # simulated restart
    assert reborn.in_cooldown("NVDA", "down_day")
    assert reborn.analyses_today() == 2


def test_expired_cooldowns_not_reloaded(tmp_path):
    from datetime import timedelta

    from smartcapital.state import Store, utcnow

    path = tmp_path / "state.json"
    first = Store(path)
    first.start_cooldown("OLD", "down_day", utcnow() - timedelta(days=1))
    first.start_cooldown("FRESH", "down_day", utcnow() + timedelta(days=1))

    reborn = Store(path)
    assert not reborn.in_cooldown("OLD", "down_day")
    assert reborn.in_cooldown("FRESH", "down_day")


def test_corrupt_state_file_starts_fresh(tmp_path):
    from smartcapital.state import Store

    path = tmp_path / "state.json"
    path.write_text("{not json")
    store = Store(path)
    assert store.cooldowns == {} and store.analyses_today() == 0
