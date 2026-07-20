"""SmartCapital v1.

Flow: predefined triggers -> gather TA + fundamentals -> LLM recommends buy
or decline -> if buy, Telegram approval request -> user approves or denies ->
if approved, limit order is placed. Nothing executes without approval.
"""

__version__ = "1.0.0"
