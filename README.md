# SmartCapital v1

A deliberately simple, human-approved investing assistant. It watches a
watchlist, and when a predefined condition hits, it gathers the facts, asks an
LLM for a buy/decline call, and — only if the LLM says buy — asks **you** on
Telegram. Nothing is ever bought without your explicit approval.

## The flow

```mermaid
flowchart LR
    T["Triggers (deterministic, full S&P 500)<br/>· down ≥ 5% on the day<br/>· crossing down through EMA-200<br/>+ cooldown, severity ranking, daily caps"]
    D["Gather details<br/>TA (from Alpaca bars)<br/>+ fundamentals, earnings dates,<br/>news headlines (FMP)"]
    L["LLM<br/>buy or decline<br/>(decline = default)"]
    A["Telegram approval<br/>Approve / Deny buttons<br/>expires after 1h"]
    O["Limit order on Alpaca<br/>only if price still in band"]
    LOGD[("logged,<br/>nothing sent")]
    LOGN[("no action")]

    T --> D --> L
    L -->|buy| A
    L -->|decline| LOGD
    A -->|approve| O
    A -->|deny / expire| LOGN
```

## Components

| File | Job |
|---|---|
| `triggers.py` | The two v1 triggers (with severity scores) + TA snapshot, pure functions, thresholds in `config.yaml` |
| `market.py` | Alpaca: bars + prices (batched for the full index), clock, cash |
| `fundamentals.py` | FMP: sector, valuation, recent + upcoming earnings, just-reported flag, news headlines |
| `analyst.py` | One LLM call → strict-JSON `buy`/`decline` with reasoning + risks |
| `telegram_bot.py` | Approve/Deny buttons in your chat; unanswered proposals expire |
| `executor.py` | Pre-submit re-checks, then a limit order; tracks to fill |
| `engine.py` | Wires the pipeline |
| `state.py` | Proposals + event log in memory; cooldowns + daily budget persisted to a JSON state file |
| `cli.py` | `smartcapital run` |

## Behavior notes

- **Human gate**: every buy needs a tap in your Telegram chat; unanswered
  proposals expire into nothing after an hour.
- **Price band**: your approval means "buy near $X". If the price has moved
  outside ±1% by execution time, the proposal is voided, not chased.
- **Limit orders only**, sized at a fixed $ amount, with a cash floor.
- **Cooldown**: an analyzed trigger fires once per symbol per 5 days.
- **Scale throttles**: the full S&P 500 is scanned (~10 batched API calls per
  cycle), but triggers are ranked by severity and capped (default 3 per cycle,
  6 per day) so a red market day can't flood your Telegram or the FMP/LLM budget.
- **`ALPACA_ENV` is required** — set it to `paper` or `live` yourself; there
  is no default.
- **Restart-safe**: cooldowns and the daily analysis budget persist to a small
  JSON state file (`STATE_FILE`, default `.state.json`), so a restart mid-day
  can't re-analyze or re-ping. Pending proposals die with the process, which
  is safe - nothing survives to execute unexpectedly.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env               # Alpaca, FMP, Anthropic, Telegram keys
cp config.example.yaml config.yaml # watchlist + thresholds
smartcapital run
```

Telegram setup: create a bot with @BotFather, put its token in `.env`, message
the bot once, then put your chat id in `TELEGRAM_CHAT_ID` (get it from
`https://api.telegram.org/bot<TOKEN>/getUpdates`).

## Tests

```bash
pytest
```

## Deployment

See [deploy/azure-container-apps.md](deploy/azure-container-apps.md) for running
this on Azure Container Apps (single always-on replica, Azure Files-backed
state, secrets as ACA secrets). A `Dockerfile` is included.

## Roadmap (kept out of v1 on purpose)

Options-volume trigger · sell/portfolio-review loop · persistence across
restarts · exposure/sector limits · kill switch · evaluation scoring vs a
no-LLM baseline. A fuller v2 design exploring all of these lives in git
history.

> **Not investment advice.** Personal tooling; a human approves every action.
