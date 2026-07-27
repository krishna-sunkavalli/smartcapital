"""Telemetry must be a safe no-op when App Insights isn't configured, so the
pipeline never fails for a monitoring reason."""
from smartcapital import telemetry


def test_setup_returns_false_without_connection_string(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert telemetry.setup_telemetry() is False


def test_helpers_are_noops_when_disabled(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    telemetry.setup_telemetry()
    # None of these should raise when telemetry is disabled.
    telemetry.heartbeat()
    telemetry.record_decision("AAPL", "decline")
    telemetry.record_proposal("AAPL")
    telemetry.record_order_submitted("AAPL")
    telemetry.record_order_failed("AAPL")
    telemetry.record_llm_usage({"input_tokens": 100, "output_tokens": 50}, "gpt-5-mini")
