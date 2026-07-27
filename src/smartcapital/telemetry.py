"""Observability: OpenTelemetry metrics + traces exported to Azure Application
Insights (Azure Monitor).

Everything here is best-effort and safe to call unconditionally. If
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is unset, or the azure-monitor
package isn't installed (e.g. in a unit-test environment), telemetry silently
becomes a no-op so the pipeline never fails for a monitoring reason.

Emitted signals (kept deliberately lean to cap ingestion cost):
- ``smartcapital.heartbeat``        counter  - liveness; alert if it stops
- ``smartcapital.decisions``        counter  - LLM verdicts (verdict=buy|decline)
- ``smartcapital.proposals``        counter  - buy proposals sent for approval
- ``smartcapital.orders.submitted`` counter  - limit orders submitted
- ``smartcapital.orders.failed``    counter  - order submit failures (alert)
- ``smartcapital.llm.tokens``       counter  - LLM tokens (in/out via attribute)
- ``smartcapital.llm.cost_usd``     counter  - estimated LLM spend (cost alert)
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Rough Azure OpenAI gpt-5-mini pricing (USD per 1M tokens). Only used to emit an
# estimated cost metric for the Azure Monitor budget alert; not
# billing-authoritative. Update if the deployed model changes.
_USD_PER_1M_INPUT = 0.25
_USD_PER_1M_OUTPUT = 2.0

_enabled = False
_heartbeat = _decisions = _proposals = _orders_submitted = _orders_failed = None
_llm_tokens = _llm_cost = None


def setup_telemetry() -> bool:
    """Configure Azure Monitor + create metric instruments. Returns True if
    telemetry is active. Idempotent and never raises."""
    global _enabled, _heartbeat, _decisions, _proposals, _orders_submitted, _orders_failed
    global _llm_tokens, _llm_cost
    if _enabled:
        return True
    try:
        import os

        if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
            log.info("App Insights connection string not set; telemetry disabled")
            return False

        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry import metrics

        configure_azure_monitor(logger_name="smartcapital")
        meter = metrics.get_meter("smartcapital")
        _heartbeat = meter.create_counter("smartcapital.heartbeat", unit="1",
                                          description="scheduler liveness ticks")
        _decisions = meter.create_counter("smartcapital.decisions", unit="1",
                                          description="LLM verdicts (verdict=buy|decline)")
        _proposals = meter.create_counter("smartcapital.proposals", unit="1",
                                          description="buy proposals sent for approval")
        _orders_submitted = meter.create_counter("smartcapital.orders.submitted", unit="1",
                                                 description="limit orders submitted")
        _orders_failed = meter.create_counter("smartcapital.orders.failed", unit="1",
                                              description="order submit failures")
        _llm_tokens = meter.create_counter("smartcapital.llm.tokens", unit="1",
                                           description="LLM tokens consumed")
        _llm_cost = meter.create_counter("smartcapital.llm.cost_usd", unit="USD",
                                         description="estimated LLM spend")
        _enabled = True
        log.info("Application Insights telemetry configured")
    except Exception:
        log.exception("telemetry setup failed; continuing without it")
        _enabled = False
    return _enabled


def heartbeat() -> None:
    if _enabled and _heartbeat is not None:
        _heartbeat.add(1)


def record_decision(symbol: str, verdict: str) -> None:
    """Every LLM verdict, buy or decline - so decline volume is queryable."""
    if _enabled and _decisions is not None:
        _decisions.add(1, {"symbol": symbol, "verdict": verdict})


def record_proposal(symbol: str) -> None:
    if _enabled and _proposals is not None:
        _proposals.add(1, {"symbol": symbol})


def record_order_submitted(symbol: str) -> None:
    if _enabled and _orders_submitted is not None:
        _orders_submitted.add(1, {"symbol": symbol})


def record_order_failed(symbol: str) -> None:
    if _enabled and _orders_failed is not None:
        _orders_failed.add(1, {"symbol": symbol})


def record_llm_usage(usage: dict | None, model: str) -> None:
    """Emit token counts and an estimated cost for the LLM budget alert."""
    if not (_enabled and _llm_tokens is not None):
        return
    usage = usage or {}
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    _llm_tokens.add(inp, {"direction": "input", "model": model})
    _llm_tokens.add(out, {"direction": "output", "model": model})
    cost = inp / 1_000_000 * _USD_PER_1M_INPUT + out / 1_000_000 * _USD_PER_1M_OUTPUT
    if _llm_cost is not None and cost:
        _llm_cost.add(cost, {"model": model})
