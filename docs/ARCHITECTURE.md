# Architecture Diagrams

## 1. System overview — layered authority

Deterministic code owns monitoring, math, safety, and execution. The LLM owns
analysis and recommendation only. The human owns every decision. Alpaca
executes only approved, price-bounded, idempotent orders.

```mermaid
flowchart TB
    subgraph DATA["Data layer — every record carries source + as-of"]
        ALP["Market data<br/>(Alpaca)<br/>prices · clock · account"]
        FMP["Fundamentals<br/>(FMP)<br/>earnings · ratios · sector · S&P 500 list"]
        EVT["Structured events<br/>(FMP calendar + SEC EDGAR)<br/>earnings dates · filings only"]
    end

    subgraph ENGINE["Strategy engine (deterministic)"]
        IND["Indicator math<br/>EMA · RSI · MACD"]
        TRIG["Trigger detection<br/>buy-side + portfolio-review"]
        DEDUP["Dedup + cooldown<br/>(persisted, per symbol+type)"]
        BLACK["Earnings blackout<br/>(buys only)"]
    end

    subgraph ANALYST["AI analyst (recommends, never decides)"]
        PKT["Packet validation<br/>missing → reject<br/>stale → reject<br/>feeds disagree → halt"]
        BEAR["Bear pass<br/>argue against"]
        BULL["Bull pass<br/>argue for"]
        JUDGE["Judge pass<br/>strict JSON verdict"]
    end

    subgraph GUARD["Deterministic guardrails — LLM cannot override"]
        G1["universe · long-only · exposure caps<br/>deployment limits · cash buffer<br/>auction windows · price band<br/>kill switch · anomaly halt"]
    end

    subgraph HUMAN["Human gate (Telegram, hardened)"]
        TG["Allowlisted chat · signed single-use nonce<br/>bound to proposal + price band<br/>read delay · type-back · daily cap"]
    end

    subgraph EXEC["Execution"]
        RECHECK["Re-run ALL guardrails<br/>on refreshed state"]
        ORDER["Idempotent limit order<br/>(persisted client order id)"]
        LIFE["Lifecycle tracking<br/>fill / partial / expiry"]
    end

    DB[("Database<br/>proposals · cooldowns · approvals<br/>orders · positions · flags")]
    AUDIT[("Append-only<br/>audit log")]

    ALP --> IND --> TRIG
    TRIG --> DEDUP --> BLACK --> PKT
    FMP --> PKT
    EVT --> PKT
    ALP --> PKT
    PKT --> BEAR --> BULL --> JUDGE
    JUDGE -->|"Buy / Trim / Sell"| GUARD
    JUDGE -->|"Watch / Pass / Hold<br/>(logged for scoring, no action)"| DB
    GUARD -->|all checks pass| TG
    GUARD -->|violation → voided,<br/>human never sees it| DB
    TG -->|approved| RECHECK
    TG -->|rejected / expired → no action| DB
    RECHECK -->|still clean| ORDER --> LIFE
    RECHECK -->|"price left band, etc.<br/>→ VOID, never resubmit"| DB
    ORDER --> BROKER["Alpaca<br/>(paper / live)"]
    LIFE --> DB
    DEDUP -.-> DB
    JUDGE -.-> AUDIT
    TG -.-> AUDIT
    ORDER -.-> AUDIT
```

## 2. End-to-end flow — one trade

```mermaid
sequenceDiagram
    autonumber
    participant M as Market/Fundamentals/Events feeds
    participant E as Strategy engine
    participant L as LLM (bear→bull→judge)
    participant G as Guardrails
    participant T as Telegram bot
    participant H as Human
    participant X as Executor
    participant A as Alpaca

    E->>M: poll daily bars + latest price
    E->>E: detect trigger (e.g. EMA-20/50 cross)
    E->>E: cooldown check (persisted) — refire? stop here
    E->>E: earnings blackout check (buys)
    E->>E: log control-baseline alert (no-LLM arm, same price)
    E->>M: assemble packet (source + as-of on every field)
    E->>E: validate: missing/stale → REJECT, feeds disagree → HALT
    E->>L: bear pass (argue against)
    E->>L: bull pass (argue for)
    E->>L: judge pass → strict JSON
    Note over L: every verdict persisted with<br/>hypothetical entry price (incl. Pass/Watch)
    alt verdict is Buy / Trim / Sell
        E->>G: check ALL guardrails (proposal time)
        alt violation
            G-->>E: proposal VOIDED — human never pinged
        else clean
            E->>T: proposal + bear/bull cases + risks + band
            T->>H: message (buttons inert for read-delay)
            alt approve (+ ticker type-back if large)
                H->>T: signed single-use nonce, band-bound
                T->>X: proposal APPROVED
                X->>M: refresh price + account
                X->>G: re-run ALL guardrails
                alt price left approved band
                    G-->>X: VOID — never resubmitted
                else still in band
                    X->>A: limit order (idempotent client order id)
                    A-->>X: lifecycle: fill / partial / expiry
                    X->>X: persist every transition + audit
                end
            else reject / expire
                T-->>E: no action, logged
            end
        end
    else Watch / Pass / Hold
        E->>E: logged for later scoring — nothing sent
    end
```

## 3. Proposal lifecycle

```mermaid
stateDiagram-v2
    [*] --> PENDING : analysis actionable +<br/>guardrails clean
    PENDING --> APPROVED : human approves<br/>(signed nonce, band-bound,<br/>after read delay / type-back)
    PENDING --> REJECTED : human rejects
    PENDING --> EXPIRED : TTL elapses<br/>(server-side sweep)
    PENDING --> VOIDED : guardrail violation
    APPROVED --> EXECUTED : pre-execution re-check clean →<br/>limit order submitted
    APPROVED --> VOIDED : price left band /<br/>any guardrail fails →<br/>never resubmitted
    REJECTED --> [*] : no action
    EXPIRED --> [*] : no action
    VOIDED --> [*] : no action, audit-logged
    EXECUTED --> [*] : order lifecycle tracked<br/>to fill / partial / expiry
```

## 4. Trigger taxonomy

```mermaid
flowchart LR
    subgraph BUY["Buy-side (v2)"]
        B1["EMA-200<br/>approach / cross"]
        B2["EMA-20/50<br/>crossover"]
        B3["RSI oversold ·<br/>MACD bull cross"]
        B4["Pullback +<br/>volume confirmation"]
    end
    subgraph REV["Portfolio review (new in v2)"]
        R1["Weekly scheduled<br/>review"]
        R2["Drawdown beyond<br/>threshold"]
        R3["Concentration beyond<br/>limits"]
        R4["Earnings released<br/>for held name"]
        R5["Thesis-break:<br/>original conditions re-verified"]
    end
    subgraph DEFER["Deferred to v2.1"]
        D1["Unusual options activity"]
        D2["IV signals"]
        D3["Unstructured news / macro"]
    end
    BUY --> AN["Analysis<br/>(Buy / Watch / Pass)"]
    REV --> AN2["Analysis<br/>(Hold / Trim / Sell)"]
    style DEFER stroke-dasharray: 5 5
```

## 5. Evaluation and staged rollout

```mermaid
flowchart LR
    P1["Phase 1 — PAPER<br/>≥ 3 months<br/>score every verdict incl. Pass/Watch<br/>vs no-LLM control baseline"]
    P2["Phase 2 — LIVE<br/>training wheels:<br/>small fixed sizes, reduced caps<br/>(enforced in code by rollout.phase)"]
    P3["Phase 3 — LIVE<br/>full configured limits"]
    FB["Fallback design:<br/>remove LLM layer, ship as<br/>deterministic alerting tool"]

    P1 -->|"frozen promotion criteria met<br/>(human decision, code never<br/>promotes itself)"| P2
    P2 -->|"clean period: no guardrail violations,<br/>no unexplained behavior"| P3
    P1 -->|criteria not met| FB
    P2 -->|violations / anomalies| FB
```
