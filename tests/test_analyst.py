import json
from types import SimpleNamespace

from smartcapital import analyst
from smartcapital.analyst import VERDICT_SCHEMA, parse_verdict
from smartcapital.config import LlmCfg


def test_valid_verdict_passes_through():
    v = parse_verdict(json.dumps({
        "recommendation": "buy",
        "reasoning": "Strong support at EMA-200 with cheap valuation.",
        "key_risks": ["earnings in 9 days"],
        "confidence": "medium",
    }))
    assert v["recommendation"] == "buy"
    assert v["key_risks"] == ["earnings in 9 days"]


def test_garbage_text_declines():
    v = parse_verdict("I think you should probably buy this one!")
    assert v["recommendation"] == "decline"


def test_missing_recommendation_declines():
    v = parse_verdict(json.dumps({"reasoning": "hmm"}))
    assert v["recommendation"] == "decline"


def test_fenced_json_is_parsed():
    v = parse_verdict('```json\n{"recommendation": "buy", "reasoning": "ok", '
                      '"key_risks": [], "confidence": "low"}\n```')
    assert v["recommendation"] == "buy"


def test_recommendation_is_case_normalised():
    v = parse_verdict(json.dumps({
        "recommendation": "DECLINE", "reasoning": "x",
        "key_risks": [], "confidence": "low"}))
    assert v["recommendation"] == "decline"


def test_prose_wrapped_json_is_extracted():
    v = parse_verdict('Here is my verdict:\n{"recommendation": "Buy", '
                      '"reasoning": "held support", "key_risks": [], "confidence": "high"} '
                      'Hope that helps!')
    assert v["recommendation"] == "buy"
    assert v["confidence"] == "high"


def test_model_key_synonyms_are_accepted():
    # gpt-5-mini observed returning decision/verdict/rationale + numeric confidence.
    v = parse_verdict(json.dumps({
        "ticker": "WDC", "decision": "DECLINE",
        "confidence": 0.75, "rationale": "sector-wide memory selloff"}))
    assert v["recommendation"] == "decline"
    assert v["reasoning"] == "sector-wide memory selloff"
    assert v["confidence"] == "high"  # 0.75 -> high


def test_verdict_key_and_low_numeric_confidence():
    v = parse_verdict(json.dumps({
        "verdict": "buy", "rationale": "clear bounce",
        "confidence": 0.3, "risks": ["earnings soon"]}))
    assert v["recommendation"] == "buy"
    assert v["confidence"] == "low"  # 0.3 -> low
    assert v["key_risks"] == ["earnings soon"]


def test_schema_is_strict():
    # additionalProperties: false + full required list is what lets the API
    # guarantee the shape; guard against accidental loosening.
    assert VERDICT_SCHEMA["additionalProperties"] is False
    assert set(VERDICT_SCHEMA["required"]) == {
        "recommendation", "reasoning", "key_risks", "confidence"}
    assert VERDICT_SCHEMA["properties"]["recommendation"]["enum"] == ["buy", "decline"]


class _FakeResponses:
    def __init__(self, output_text):
        self._text = output_text

    def create(self, **_kw):
        return SimpleNamespace(
            output_text=self._text,
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
        )


class _FakeProject:
    def __init__(self, output_text):
        self._client = SimpleNamespace(responses=_FakeResponses(output_text))

    def get_openai_client(self):
        return self._client


def test_analyze_parses_foundry_response(monkeypatch):
    verdict_json = json.dumps({
        "recommendation": "buy",
        "reasoning": "Held EMA-200 on heavy volume with a reasonable multiple.",
        "key_risks": ["earnings in 6 days"],
        "confidence": "medium",
    })
    monkeypatch.setattr(analyst, "_ensure_agent",
                        lambda cfg: (_FakeProject(verdict_json), "smartcapital-analyst"))

    v = analyst.analyze("AAPL", "down_day", {"pct": -0.06}, {"technicals": {}}, LlmCfg())

    assert v["recommendation"] == "buy"
    assert v["model"] == LlmCfg().model
    assert v["usage"] == {"input_tokens": 1200, "output_tokens": 300}


def test_analyze_declines_on_garbage(monkeypatch):
    monkeypatch.setattr(analyst, "_ensure_agent",
                        lambda cfg: (_FakeProject("not json at all"), "smartcapital-analyst"))

    v = analyst.analyze("AAPL", "down_day", {}, {}, LlmCfg())

    assert v["recommendation"] == "decline"


def test_dry_run_returns_stub_without_azure(monkeypatch):
    # dry-run must never touch _ensure_agent (no Azure/Foundry dependency).
    def _boom(_cfg):
        raise AssertionError("_ensure_agent must not be called in dry-run")

    monkeypatch.setattr(analyst, "_ensure_agent", _boom)

    v = analyst.analyze("AAPL", "down_day", {"pct": -0.06}, {"technicals": {}},
                        LlmCfg(dry_run=True))

    assert v["recommendation"] == "buy"  # exercises the approval + order path
    assert "dry-run" in v["model"]
    assert v["usage"] == {"input_tokens": None, "output_tokens": None}
    # still schema-valid so the engine handles it like a real verdict
    assert set(VERDICT_SCHEMA["required"]).issubset(v.keys())

