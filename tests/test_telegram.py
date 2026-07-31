"""Tests that the Telegram message escapes untrusted LLM content so it can never
break parsing and silently drop a proposal."""
from datetime import datetime, timezone

from smartcapital.state import Proposal, Status
from smartcapital.telegram_bot import format_message


def make_proposal(**kw) -> Proposal:
    defaults = dict(
        symbol="AAPL", trigger_type="down_day", trigger_details={},
        packet={"technicals": {}, "fundamentals": {}},
        llm_model="m", llm_verdict={}, reference_price=100.0,
        limit_low=99.0, limit_high=101.0, qty=1.0, notional=100.0,
        status=Status.PENDING,
        expires_at=datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc),
    )
    defaults.update(kw)
    return Proposal(**defaults)


def test_html_metacharacters_in_llm_output_are_escaped():
    p = make_proposal(llm_verdict={
        "reasoning": "buy <b>now</b> & 5 > 3",
        "key_risks": ["a < b", "tom & jerry"],
        "confidence": "high",
    })
    msg = format_message(p)

    # Injected markup must be escaped, not passed through as live tags.
    assert "buy &lt;b&gt;now&lt;/b&gt;" in msg
    assert "5 &gt; 3" in msg
    assert "a &lt; b" in msg
    assert "tom &amp; jerry" in msg
    # The template's own bold tags remain real.
    assert "<b>BUY · AAPL</b>" in msg


def test_missing_expiry_does_not_raise():
    p = make_proposal(expires_at=None)
    msg = format_message(p)
    assert "expires —" in msg
