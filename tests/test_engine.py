"""Tests that the analysis pipeline commits cooldown + daily budget only after a
successful verdict, so a transient data/LLM error doesn't burn the slot."""
import pytest

from smartcapital.config import Config
from smartcapital.engine import Engine
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
