"""Single-pass LLM analyst: given the trigger and the data packet, recommend
BUY or DECLINE with reasoning and risks. Strict JSON out; anything malformed
is treated as DECLINE. The model must use only packet data - never memory -
for prices and fundamentals.
"""
from __future__ import annotations

import json
import re

from anthropic import Anthropic

from smartcapital.config import LlmCfg, secrets

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

Data packet (technicals + fundamentals):
{packet}

Respond with STRICT JSON only:
{{
  "recommendation": "buy" | "decline",
  "reasoning": "<3-5 sentences>",
  "key_risks": ["...", "..."],
  "confidence": "low" | "medium" | "high"
}}"""


def analyze(symbol: str, trigger_type: str, trigger_details: dict, packet: dict,
            cfg: LlmCfg) -> dict:
    client = Anthropic(api_key=secrets().anthropic_api_key)
    msg = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        system=SYSTEM,
        messages=[{"role": "user", "content": PROMPT.format(
            symbol=symbol, trigger_type=trigger_type,
            trigger_details=json.dumps(trigger_details),
            packet=json.dumps(packet, indent=2, default=str))}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    verdict = _parse(text)
    verdict["model"] = cfg.model
    return verdict


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"recommendation": "decline", "reasoning": "unparseable model output",
                "key_risks": [], "confidence": "low", "raw": text[:500]}
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"recommendation": "decline", "reasoning": "invalid JSON from model",
                "key_risks": [], "confidence": "low", "raw": text[:500]}
    if str(v.get("recommendation", "")).lower() not in ("buy", "decline"):
        v["recommendation"] = "decline"
    v["recommendation"] = v["recommendation"].lower()
    return v
