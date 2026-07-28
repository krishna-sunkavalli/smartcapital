"""Tests that the analysis pipeline commits cooldown + daily budget only after a
successful verdict, so a transient data/LLM error doesn't burn the slot."""
import pandas as pd
import pytest

from smartcapital.config import Config
from smartcapital.engine import Engine, _rank_score
from smartcapital.state import Store
from smartcapital.triggers import Trigger


def _engine(tmp_path, monkeypatch) -> tuple[Engine, Store]:
    monkeypatch.setattr("smartcapital.engine.Market", lambda: object())
    monkeypatch.setattr("smartcapital.engine.triggers.ta_snapshot", lambda df, price: {})
    monkeypatch.setattr("smartcapital.engine.fundamentals.snapshot", lambda s: {})
    monkeypatch.setattr("smartcapital.engine.fundamentals.news", lambda s: [])
    store = Store(path=tmp_path / "state.json")
    return Engine(Config(), store), store


TRIG = Trigger(trigger_type="down_day", details={"pct": -0.06}, severity=0.06)


def test_error_does_not_burn_cooldown_or_budget(tmp_path, monkeypatch):
    engine, store = _engine(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("smartcapital.engine.analyst.analyze", boom)

    with pytest.raises(RuntimeError):
        engine._analyze("AAPL", TRIG, df=None, price=100.0)

    assert store.analyses_today() == 0
    assert store.in_cooldown("AAPL", "down_day") is False


def test_success_commits_cooldown_and_budget(tmp_path, monkeypatch):
    engine, store = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr("smartcapital.engine.analyst.analyze",
                        lambda *a, **k: {"recommendation": "decline", "model": "m"})

    engine._analyze("AAPL", TRIG, df=None, price=100.0)

    assert store.analyses_today() == 1
    assert store.in_cooldown("AAPL", "down_day") is True


def _bars(volume: float) -> pd.DataFrame:
    return pd.DataFrame({"volume": [volume] * 20})


def test_rank_score_favours_liquid_names_over_deeper_microcap_drop():
    # A mega-cap down 5% should outrank an illiquid micro-cap down 7%.
    mega = _rank_score(Trigger("down_day", {}, severity=0.05), _bars(80_000_000), 200.0)
    micro = _rank_score(Trigger("down_day", {}, severity=0.07), _bars(50_000), 15.0)
    assert mega > micro


def test_rank_score_severity_dominates_within_a_size_tier():
    # Same liquidity: the bigger drop wins.
    df, price = _bars(60_000_000), 100.0
    deep = _rank_score(Trigger("down_day", {}, severity=0.09), df, price)
    shallow = _rank_score(Trigger("down_day", {}, severity=0.05), df, price)
    assert deep > shallow


def test_rank_score_handles_nan_volume():
    score = _rank_score(Trigger("down_day", {}, severity=0.06), _bars(float("nan")), 100.0)
    assert score > 0  # floored liquidity, ranks on severity

