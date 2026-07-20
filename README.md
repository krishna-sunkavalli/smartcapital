# Human-Approved AI Investment System (v2)

A low-cost investment assistant for a small, aggressive portion of savings.
S&P 500 companies only, preference for large-cap technology and growth. The
system monitors the market, detects opportunities, performs multi-factor
adversarial analysis, recommends **buy / trim / sell** actions — and executes
**nothing** without explicit, authenticated human approval.

## Core design principle: layered authority

| Layer | Owns |
|---|---|
| Deterministic code | Monitoring, math, safety, execution |
| LLM (adversarial: bear → bull → judge) | Analysis and recommendation only — treated as an unproven hypothesis until paper-trading data says otherwise |
| Human (hardened Telegram gate) | Every buy, trim, and sell decision, with fatigue countermeasures to keep that ownership real |
| Alpaca | Only explicitly approved, price-bounded, idempotent limit orders |

## Layout

```
src/aiis/
├── config.py            # typed YAML limits + .env secrets (LLM can't touch either)
├── db/                  # persisted state: proposals, cooldowns, approvals, orders, audit log
├── data/                # 3 feeds, every record stamped source + as-of
│   ├── market.py        #   Alpaca: prices, clock, account
│   ├── fundamentals.py  #   FMP: earnings, estimates, ratios, sector, S&P500 list
│   └── events.py        #   structured only: earnings calendar + SEC filings
├── triggers/            # deterministic detection; dedup/cooldown BEFORE any LLM call
│   ├── buy_triggers.py  #   EMA-200 touch, 20/50 cross, RSI/MACD, pullback+volume
│   ├── review_triggers.py # weekly / drawdown / concentration / earnings / thesis-break
│   └── blackout.py      #   no buys within 5 trading days of earnings
├── analyst/             # adversarial 3-pass LLM (bear → bull → judge), pinned versions
├── guardrails/          # pure-function checks, run at proposal time AND pre-execution
├── approval/            # hardened Telegram: signed single-use nonces, band-bound,
│                        # read-delay, type-back, daily cap, gate-alarm digest
├── execution/           # idempotent limit orders (client order ids), lifecycle, audit
├── strategy/            # engine pipeline + Lumibot adapter (trigger-only backtests)
└── evaluation/          # scoring vs no-LLM control, staged-rollout promotion report
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add [lumibot] for the Lumibot adapter
cp .env.example .env               # fill in keys
cp config/config.example.yaml config/config.yaml
aiis init-db
```

## Running

```bash
aiis run --watchlist AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,CRM
aiis kill --reason "stepping away"   # kill switch: disables all proposals immediately
aiis unkill
aiis digest                          # weekly approval-rate digest (gate alarm)
aiis score                           # score ALL recommendations (incl. Pass/Watch) vs control
aiis phase1-report --started 2026-08-01
```

## The flow, end to end

1. **Trigger** fires (deterministic), deduplicated per (symbol, type) with a
   persisted cooldown; earnings blackout suppresses buy triggers near reports.
2. **Control baseline** logged first: "trigger fired, alert sent, no LLM" at
   the same reference price — the LLM must beat this arm to earn its keep.
3. **Data packet** assembled from the three feeds; every field carries source
   and as-of. Missing or stale fields **reject the analysis**; materially
   disagreeing feeds **halt** it.
4. **Adversarial analysis**: bear pass argues against, bull pass argues for,
   judge outputs strict JSON (Buy/Watch/Pass or Hold/Trim/Sell) with reasoning,
   risks, size, limit reference and measurable thesis conditions. Model +
   prompt versions pinned and recorded; temperature 0 or 3-sample majority
   vote; confidence is a label, not a probability. Every result — including
   Pass and Watch — is stored with a hypothetical entry price for scoring.
5. **Guardrails** (proposal time): universe, long-only, position/sector/
   correlated-cluster caps, managed-capital ceiling, daily/weekly deployment,
   cash buffer, auction windows, anomaly halt, kill switch. Violations mean
   the human never even sees a proposal.
6. **Telegram approval**: full packet summary with data timestamps, bear +
   bull cases, risks. Signed single-use nonce bound to the proposal **and its
   price band**; buttons inert for a read delay; big orders require typing the
   ticker; daily approval cap; proposals expire harmlessly.
7. **Execution**: refresh state, **re-run every guardrail**; if price left the
   approved band the proposal is **voided, never resubmitted**. Limit order
   only, idempotent client order id persisted before the network call. Full
   lifecycle (fill / partial / expiry) persisted; positions updated on fill.
8. **Audit**: append-only log records the packet, all three LLM passes, the
   approval event, and the order result.

## Evaluation & rollout

LLM judgment cannot be backtested honestly (the model knows historical
outcomes), so: **Phase 1** paper ≥ 3 months scoring every recommendation
against the no-LLM control; **Phase 2** live with training-wheel limits
(small fixed order sizes, reduced caps — enforced in code by `rollout.phase`);
**Phase 3** full limits only after Phase 2 runs clean. Promotion criteria are
frozen in `evaluation/rollout.py` before Phase 1 starts. If they're not met,
the LLM layer is removed and this ships as a deterministic alerting tool —
that is the designed fallback, not a failure.

Backtesting (`strategy/lumibot_strategy.py::TriggerBacktest`) covers the
trigger layer only.

## Deferred to v2.1

Unusual options activity, implied-volatility signals, unstructured news and
macro-headline triggers.

## Tests

```bash
pytest
```

Covers indicators, buy/review trigger conditions, cooldown/dedup, earnings
blackout, every guardrail rule, packet freshness/missing-data/feed-disagreement
rejection, approval callback signing/binding, read-delay, type-back, and the
gate alarm.

> **Not investment advice.** This is personal tooling for a small, explicitly
> aggressive slice of savings, with a human approving every action.
