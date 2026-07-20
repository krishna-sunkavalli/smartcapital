# Human-Approved AI Investment System (v2) — Design Specification

## Objective

A low-cost investment assistant for a small, aggressive portion of savings.
S&P 500 companies only, with a preference for large-cap technology and growth.
The system monitors the market, detects opportunities, performs multi-factor
analysis, recommends buy, trim, or sell actions, and executes nothing without
explicit, authenticated human approval.

**v2 changes from v1:** adversarial LLM framing, a full sell/portfolio-review
loop, a dedicated fundamentals data layer, correlation and sector exposure
controls, approval-fatigue countermeasures, hardened Telegram approvals,
idempotent execution, and a staged rollout with predefined success criteria.
Options analysis and unstructured-news triggers are deferred to v2.1.

## Architecture

### 1. Data layer

Split into three feeds, each with freshness metadata attached to every record:

- **Market data (Alpaca):** historical and real-time prices, corporate
  actions, order execution (paper and live).
- **Fundamentals (dedicated provider, e.g. Financial Modeling Prep, Polygon,
  or EODHD):** earnings history, guidance, analyst estimates, valuation
  ratios, sector classification. Alpaca does not provide fundamentals; the
  LLM is never allowed to supply them from memory.
- **Structured events:** earnings calendar and SEC filings only. Unstructured
  news and macro headlines are excluded from v2 triggers.

Every data point injected into the LLM prompt carries a source and an as-of
timestamp. Analysis is rejected if any required field is missing or stale
(freshness thresholds per feed).

### 2. Strategy engine (Lumibot)

- Scheduled and event-driven checks
- Technical indicator calculation
- Trigger detection with per-symbol deduplication and cooldown BEFORE the LLM
  is invoked (an EMA cross fires once, not every polling cycle)
- Backtesting of the trigger layer only (LLM decisions are explicitly not
  backtestable; see Evaluation)
- Persisted state in a database, not memory: open proposals, cooldowns,
  approvals, order lifecycle. The process must survive a restart mid-flow.

### 3. Trigger layer

Triggers initiate analysis; they never make the decision.

**Buy-side triggers (v2):**

- Price approaching or crossing EMA-200
- EMA-20/50 crossover
- RSI or MACD condition
- Significant pullback with volume confirmation

**Portfolio-review triggers (new):**

- Weekly scheduled review of every open position
- Position drawdown beyond threshold
- Position gain pushing concentration beyond limits
- Earnings report released for a held name
- Thesis-break check: the conditions cited in the original buy analysis are
  re-verified

**Deferred to v2.1:** unusual options activity, implied-volatility signals,
unstructured news and macro headlines. These are noisy, hard to detect
reliably, and each is a project in itself.

**Blackout rule:** no new buy proposals within 5 trading days before a
scheduled earnings report. Earnings are a review trigger for held positions,
not a buy trigger.

### 4. AI analyst (adversarial structure)

The LLM layer is restructured to counter trigger-anchoring bias:

1. **Bear pass:** given the full data packet, the model must first argue why
   this trade should NOT be made (or why a held position should be exited).
2. **Bull pass:** the model then makes the strongest affirmative case.
3. **Judge pass:** a final step weighs both and outputs the recommendation.

Output for buy-side analysis: Buy / Watch / Pass. Output for portfolio
review: Hold / Trim / Sell. A Buy, Trim, or Sell includes reasoning, key
risks, proposed size, and a limit price band.

**Determinism and evaluation controls:**

- Model version and prompt version pinned and recorded with every analysis
- Temperature 0, or 3 samples with majority vote
- Stated confidence is treated as a label, not a probability, until
  calibration data exists
- Base-rate monitor: rolling Buy/Watch/Pass distribution is tracked. If Buy
  exceeds a threshold share of analyses, the system flags itself for prompt
  review.
- Every recommendation, including Pass and Watch, is logged with a
  hypothetical entry price so all decisions can be scored later

### 5. Deterministic guardrails

The LLM cannot override any of these. Checked at proposal time and again
immediately before execution.

**Universe and instruments:** S&P 500 securities only; long equity only; no
margin, shorting, or options execution.

**Exposure:** maximum position size per name; maximum sector exposure (the
large-cap tech preference makes this essential — ten in-limit positions can
still be one correlated Nasdaq bet); hard ceiling on total system-managed
capital as a fraction of the account, separate from flow limits; maximum
daily and weekly deployment; available-cash verification.

**Order discipline:** limit orders only, priced from the approved band —
market orders prohibited; price-tolerance check immediately before
submission — if price has left the approved band, the proposal is voided,
not resubmitted; no orders during halts or within the opening/closing
auction windows; idempotent submission via client order IDs — duplicate
prevention is persisted, not in-memory.

**Data integrity:** freshness requirements per feed; rejection when required
data is missing; halt if feeds materially disagree with each other.

**Kill switch and anomaly halt:** a single command disables all proposals
immediately; automatic halt if proposals exceed N in a rolling window, or if
market-wide circuit breakers trip.

### 6. Telegram approval (hardened)

Each actionable recommendation sends: ticker, action, proposed size, limit
price band, originating trigger, technical and fundamental assessment with
data timestamps, bear case, bull case, principal risks, and recommendation.

**Security:** bot restricted to a single allowlisted chat ID; callback
payloads signed; approvals are single-use nonces; approval is bound to the
specific proposal AND its price band (approve MSFT at $412 ± 1%, not
"approve MSFT"); time-limited — expiry or rejection causes no action.

**Approval-fatigue countermeasures:** daily cap on approvals; mandatory delay
between message delivery and button activation (forces reading, not
reflex-tapping); orders above a size threshold require typing the ticker
back, not just tapping a button; weekly digest of approval rate — a
near-100% approval rate is treated as a signal that the human gate has
stopped functioning.

### 7. Execution and audit

1. Refresh price and account state
2. Re-run all deterministic checks
3. Submit limit order to Alpaca with client order ID
4. Track order lifecycle to fill, partial fill, or expiry; persist every
   state transition
5. Record the complete data packet, all three LLM passes, the approval event,
   and the order result in an append-only audit log

### 8. Evaluation and rollout

The LLM's judgment cannot be backtested honestly (the model has knowledge of
historical outcomes), so paper trading is the only real evaluation. Success
criteria are defined BEFORE the paper period begins:

- **Phase 1, paper (minimum 3 months):** score all logged recommendations,
  including Pass and Watch, against hypothetical outcomes. Compare against
  the control baseline below. Verify base rates, guardrail behavior, and
  restart recovery.
- **Control baseline:** in parallel, log "trigger fired, alert with chart and
  data sent, no LLM" outcomes. The LLM layer must demonstrably outperform a
  well-built alert before it earns its cost and nondeterminism.
- **Phase 2, live with training-wheel limits:** small fixed order sizes,
  reduced daily caps, for a defined period.
- **Phase 3, full configured limits:** only after Phase 2 completes without
  guardrail violations or unexplained behavior.

Promotion criteria between phases are written down before Phase 1 starts. If
the criteria are not met, the LLM layer is removed and the system ships as a
deterministic alerting tool, which is the fallback design.

## Core design principle

A hybrid system with layered authority:

- Deterministic code owns monitoring, math, safety, and execution
- The LLM owns analysis and recommendation, structured adversarially and
  treated as an unproven hypothesis until paper-trading data says otherwise
- The human owns every buy, trim, and sell decision, with countermeasures to
  keep that ownership real
- Alpaca executes only explicitly approved, price-bounded, idempotent orders
