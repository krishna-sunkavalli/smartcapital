"""Anthropic client wrapper with determinism controls: pinned model, pinned
prompt version, temperature 0 (or N samples with majority vote on the judge's
recommendation).
"""
from __future__ import annotations

import json
import re
from collections import Counter

from anthropic import Anthropic

from aiis.config import AnalystCfg, secrets


class LLM:
    def __init__(self, cfg: AnalystCfg) -> None:
        self.cfg = cfg
        self._client = Anthropic(api_key=secrets().anthropic_api_key)

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            temperature=self.cfg.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    def judge(self, system: str, user: str) -> dict:
        """Run the judge pass. With samples > 1, take the majority vote on
        `recommendation` and keep the first sample that voted with the majority."""
        outputs = [parse_json_block(self.complete(system, user)) for _ in range(max(1, self.cfg.samples))]
        if len(outputs) == 1:
            return outputs[0]
        votes = Counter(o.get("recommendation") for o in outputs)
        winner, _ = votes.most_common(1)[0]
        chosen = next(o for o in outputs if o.get("recommendation") == winner)
        chosen["_majority_vote"] = {"votes": dict(votes), "samples": len(outputs)}
        return chosen


def parse_json_block(text: str) -> dict:
    """Extract the first JSON object from model output; strict-parse it."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"judge pass returned no JSON object: {text[:200]!r}")
    return json.loads(m.group(0))
