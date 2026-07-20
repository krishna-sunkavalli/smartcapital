"""Versioned prompts for the three-pass adversarial structure. PROMPT_VERSION
is recorded with every analysis; bump it on ANY wording change.

The structure exists to counter trigger-anchoring bias: the model must argue
against the trade before it is allowed to argue for it.
"""
from __future__ import annotations

import json

PROMPT_VERSION = "2.0.0"

SYSTEM = """You are the analysis layer of a human-approved investment system.
Hard rules you must never violate:
- Use ONLY the data provided in the packet. Every field carries a source and
  as-of timestamp. You must NOT supply prices, fundamentals, or news from
  memory; if something you need is not in the packet, name it as a limitation.
- You recommend; you never decide. A human approves or rejects every action.
- Universe is S&P 500 long equity only. Never suggest options, shorting,
  margin, or instruments outside the packet's symbol.
- Confidence you state is a label (low/medium/high), not a probability."""

BEAR_BUY = """A '{trigger_type}' trigger fired for {symbol}. Data packet (each field: value, source, as-of):

{packet}

TASK (bear pass): Argue why this trade should NOT be made. Steelman the
strongest case against buying {symbol} here: valuation, momentum traps,
deteriorating fundamentals, sector/concentration concerns, timing, and what
the trigger might be misreading. Do not present the bull case. 250 words max."""

BEAR_REVIEW = """A '{trigger_type}' review trigger fired for held position {symbol}. Data packet:

{packet}

TASK (bear pass): Argue why this position should be EXITED or trimmed. Steelman
the strongest case that the thesis is broken or the risk/reward has degraded.
Do not present the case for holding. 250 words max."""

BULL_BUY = """Same packet as before for {symbol}. The bear case argued:

{bear_case}

TASK (bull pass): Now make the strongest affirmative case FOR buying {symbol}
here, engaging directly with the bear points where you can. Cite only packet
data. 250 words max."""

BULL_REVIEW = """Same packet as before for held position {symbol}. The bear case argued:

{bear_case}

TASK (bull pass): Make the strongest case for HOLDING the position (or adding
nothing but not selling), engaging directly with the bear points. 250 words max."""

JUDGE_BUY = """You have the data packet, the bear case, and the bull case for {symbol}.

Bear case:
{bear_case}

Bull case:
{bull_case}

TASK (judge pass): Weigh both and output STRICT JSON only, no prose outside JSON:
{{
  "recommendation": "buy" | "watch" | "pass",
  "reasoning": "<3-5 sentences>",
  "key_risks": ["..."],
  "confidence_label": "low" | "medium" | "high",
  "proposed_size_pct_of_managed_capital": <number 0-10, only if buy>,
  "limit_reference": "latest_price",
  "thesis_conditions": [{{"metric": "<one of: price, rsi14, ema50, ema200>", "op": "gt"|"lt", "value": <number>}}]
}}
"thesis_conditions" are the 1-3 measurable conditions your recommendation
depends on; they will be deterministically re-verified later and a break will
trigger a review. If recommendation is watch or pass, still fill reasoning,
key_risks and thesis_conditions (empty list allowed)."""

JUDGE_REVIEW = """You have the data packet, bear case, and bull case for held position {symbol}.

Bear case:
{bear_case}

Bull case:
{bull_case}

TASK (judge pass): Output STRICT JSON only:
{{
  "recommendation": "hold" | "trim" | "sell",
  "reasoning": "<3-5 sentences>",
  "key_risks": ["..."],
  "confidence_label": "low" | "medium" | "high",
  "proposed_trim_pct": <number 0-100, only if trim>,
  "limit_reference": "latest_price",
  "thesis_conditions": [{{"metric": "<one of: price, rsi14, ema50, ema200>", "op": "gt"|"lt", "value": <number>}}]
}}"""


def render(template: str, **kw) -> str:
    if "packet" in kw and not isinstance(kw["packet"], str):
        kw["packet"] = json.dumps(kw["packet"], indent=2, default=str)
    return template.format(**kw)
