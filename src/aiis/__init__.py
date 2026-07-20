"""Human-Approved AI Investment System v2.

Layered authority:
- deterministic code owns monitoring, math, safety, and execution;
- the LLM owns analysis and recommendation only, structured adversarially;
- the human owns every buy, trim, and sell decision;
- the broker executes only explicitly approved, price-bounded, idempotent orders.
"""

__version__ = "2.0.0"
