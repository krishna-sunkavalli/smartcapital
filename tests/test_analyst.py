import json

from smartcapital.analyst import VERDICT_SCHEMA, parse_verdict


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


def test_schema_is_strict():
    # additionalProperties: false + full required list is what lets the API
    # guarantee the shape; guard against accidental loosening.
    assert VERDICT_SCHEMA["additionalProperties"] is False
    assert set(VERDICT_SCHEMA["required"]) == {
        "recommendation", "reasoning", "key_risks", "confidence"}
    assert VERDICT_SCHEMA["properties"]["recommendation"]["enum"] == ["buy", "decline"]
