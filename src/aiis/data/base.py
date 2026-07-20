"""Every data point injected into an LLM prompt carries a source and an as-of
timestamp. Analysis is rejected if any required field is missing or stale.
The LLM is never allowed to supply market or fundamental data from memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


class MissingDataError(Exception):
    """A required field is absent from its feed. The analysis must be rejected."""


class FreshnessError(Exception):
    """A required field is older than its feed's freshness threshold."""


@dataclass
class DataPoint:
    value: Any
    source: str          # e.g. "alpaca", "fmp", "sec-edgar"
    as_of: datetime      # provider timestamp of the observation, UTC

    def to_prompt(self) -> dict:
        return {"value": self.value, "source": self.source, "as_of": self.as_of.isoformat()}

    def require_fresh(self, name: str, max_age: timedelta, now: datetime | None = None) -> "DataPoint":
        now = now or datetime.now(timezone.utc)
        if self.value is None:
            raise MissingDataError(f"required field '{name}' is missing from feed '{self.source}'")
        age = now - self.as_of
        if age > max_age:
            raise FreshnessError(
                f"'{name}' from '{self.source}' is stale: as_of={self.as_of.isoformat()} "
                f"age={age} exceeds threshold {max_age}"
            )
        return self


@dataclass
class Packet:
    """A validated bundle of DataPoints keyed by field name, ready for the prompt."""

    fields: dict[str, DataPoint] = field(default_factory=dict)

    def add(self, name: str, dp: DataPoint) -> None:
        self.fields[name] = dp

    def validate(self, requirements: dict[str, timedelta], now: datetime | None = None) -> None:
        """Raise MissingDataError / FreshnessError if any required field fails."""
        for name, max_age in requirements.items():
            dp = self.fields.get(name)
            if dp is None:
                raise MissingDataError(f"required field '{name}' was never provided")
            dp.require_fresh(name, max_age, now=now)

    def to_prompt(self) -> dict:
        return {name: dp.to_prompt() for name, dp in self.fields.items()}
