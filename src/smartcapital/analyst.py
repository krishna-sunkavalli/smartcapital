"""Single-pass LLM analyst: given the trigger and the data packet, recommend
BUY or DECLINE with reasoning and risks.

Implementation notes (current Claude API):
- Structured outputs (output_config.format with a JSON schema) guarantee the
  response is valid JSON matching VERDICT_SCHEMA - "recommendation" can only
  ever be "buy" or "decline" at the API level.
- Adaptive thinking is set explicitly (on Opus 4.8, omitting `thinking` runs
  without thinking); the model reasons internally before answering.
- No sampling parameters: temperature/top_p/top_k are removed on this model
  family and would return a 400.
- Any degenerate outcome (refusal, truncation, unparseable text) is treated
  as DECLINE - the conservative default.
"""
from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from smartcapital.config import LlmCfg, secrets

log = logging.getLogger(__name__)

SYSTEM = """You are the analysis step of a human-approved investing assistant.
Rules:
- Use ONLY the data in the packet. Do not supply prices, fundamentals, or news
  from memory. If something important is missing, count it as a risk.
- The packet includes recent news headlines (titles only). Weigh them for
  context - especially WHY the stock may have dropped - but remember they are
  headlines, not verified facts.
- Pay attention to days_to_next_earnings: buying days before a report is a
  materially riskier proposition and should be reflected in your call.
- If fundamentals.just_reported is set, the trigger is likely the market's
  reaction to that earnings report - analyze it as such.
- You recommend; a human decides. Long equity only.
- Be conservative: DECLINE is the default; BUY needs a clear case."""

PROMPT = """A '{trigger_type}' trigger fired for {symbol}: {trigger_details}

Data packet (technicals + fundamentals + news headlines):
{packet}

Weigh the evidence and return your verdict."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": ["buy", "decline"]},
        "reasoning": {
            "type": "string",
            "description": "3-5 sentences: the case for this verdict, grounded in packet data",
        },
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["recommendation", "reasoning", "key_risks", "confidence"],
    "additionalProperties": False,
}

DECLINE = {"recommendation": "decline", "key_risks": [], "confidence": "low"}


def analyze(symbol: str, trigger_type: str, trigger_details: dict, packet: dict,
            cfg: LlmCfg) -> dict:
    client = Anthropic(api_key=secrets().anthropic_api_key)
    msg = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.effort, "format": {"type": "json_schema",
                                                        "schema": VERDICT_SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": PROMPT.format(
            symbol=symbol, trigger_type=trigger_type,
            trigger_details=json.dumps(trigger_details),
            packet=json.dumps(packet, indent=2, default=str))}],
    )

    if msg.stop_reason == "refusal":
        verdict = dict(DECLINE, reasoning="model refused the request")
    elif msg.stop_reason == "max_tokens":
        verdict = dict(DECLINE, reasoning="output truncated before a verdict was produced")
    else:
        text = next((b.text for b in msg.content if b.type == "text"), "")
        verdict = parse_verdict(text)

    verdict["model"] = cfg.model
    verdict["usage"] = {"input_tokens": msg.usage.input_tokens,
                        "output_tokens": msg.usage.output_tokens}
    return verdict


def parse_verdict(text: str) -> dict:
    """Structured outputs guarantee schema-valid JSON on a normal stop; this
    fallback exists so even an impossible malformation still means DECLINE."""
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        log.error("unparseable verdict text: %r", text[:200])
        return dict(DECLINE, reasoning="unparseable model output")
    if v.get("recommendation") not in ("buy", "decline"):
        return dict(DECLINE, reasoning="verdict missing recommendation")
    return v
