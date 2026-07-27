# SmartCapital Backlog

Issues logged from the full code + integration review (2026-07-26). Checked items
are addressed in code; unchecked remain open.

## P0 — Correctness / safety (money-affecting)
- [x] **Approved order stranded on transient submit error.** `executor.execute` set
  `p.client_order_id` BEFORE `submit_order`. If submit threw, `client_order_id`
  stayed set + status stayed APPROVED; next cycle the guard `if p.client_order_id:
  return False` blocked retry forever. Fixed: `client_order_id` is now assigned
  only after a successful submit; the deterministic id (`smartcap-{id}`) keeps the
  broker-side dedup so a retry can't double-fill. (executor.py)
- [x] **Cooldown + daily budget burned before LLM/data calls.** `engine._analyze`
  called `start_cooldown()` and `record_analysis()` before `fundamentals.snapshot`
  and `analyst.analyze`. A transient FMP/Anthropic error still consumed the 5-day
  cooldown and a daily-analysis slot. Fixed: both commits now run only after a
  successful verdict, so a failed analysis re-competes next cycle. (engine.py)

## P1 — Robustness / silent failures
- [x] **Telegram Markdown parse failures lose proposals silently.** `format_message`
  used `parse_mode="Markdown"`; LLM reasoning/risks containing `_ * [ \`` broke
  Telegram parsing and the fire-and-forget send swallowed the exception. Fixed:
  switched to HTML parse mode with `html.escape` on all dynamic content; send
  failures are caught and logged as a `proposal_send_failed` event. (telegram_bot.py)
- [x] **One bad order aborts the whole sync/execute cycle.** `sync_orders` and the
  cli job loops had no per-item try/except; a single broker exception aborted the
  remaining proposals that cycle. Fixed: per-proposal try/except in `sync_orders`
  and all three scheduler jobs. (executor.py, cli.py)
- [x] **`Store` is not thread-safe.** Mutated from three APScheduler worker threads
  and the event-loop callback; check-then-act status transitions could race. Fixed:
  added an `RLock` around mutations and an atomic `transition(expected -> new)`
  used by executor and telegram for status changes. (state.py, executor.py, telegram_bot.py)
- [ ] **No retry/backoff on FMP or Anthropic HTTP calls.** `fundamentals._get` uses
  `httpx.get(timeout=30)` + `raise_for_status()`, no retries; a 429/5xx throws.
  Add retry/backoff. (fundamentals.py, analyst.py)
- [ ] **Anthropic API currency risk.** Superseded by P12 (moving to Foundry). Verify
  model params against the chosen endpoint before live; a 400 degrades to DECLINE. (analyst.py)

## P2 — Observability / audit
- [ ] **No persistent audit trail.** `state.log()` events are in-memory only and lost
  on restart. A money-moving system needs a durable record. (state.py → Table Storage, P10/P12)
- [x] **No health check / failure alerting.** Added `telemetry.py` (Azure Monitor /
  OpenTelemetry, no-op safe): heartbeat, proposal, order submitted/failed, and LLM
  token/cost metrics. Bicep provisions an action group + scheduled-query alerts for
  heartbeat-missing, order-submit failures, and daily LLM cost. (telemetry.py, cli.py, infra/main.bicep)

## P3 — Correctness (minor / by-design)
- [ ] **`notional_usd` not a hard cap.** `qty = max(1, int(notional // price))` buys 1
  share even when price > notional. Make it a hard cap before live. (engine.py)
- [ ] **Severity scales differ across triggers.** down_day severity (~0.05) vs
  ema200_cross_down (~0.005); down-days almost always outrank EMA crosses under the
  cap. Normalize severity across trigger types. (triggers.py)
- [ ] **52w high uses close, not intraday high.** `ta_snapshot high_52w =
  close.tail(252).max()`; approximate. (triggers.py)
- [ ] **Full daily bars refetched + EMA-200 recomputed every 15-min cycle.** Daily
  bars change once per day, so `daily_bars_multi` + the EMA-200 computation are
  redundant intraday (same value 26x/day). Cost is trivial today, but at S&P-500
  scale × every cycle it's wasted Alpaca bandwidth/rate-limit. Optimize: cache
  daily bars + computed EMAs once per trading day, refetch only latest prices per
  cycle (handle new-day/holiday/split invalidation). (engine.py, market.py)

## P4 — Security
- [x] **Prompt-injection via news headlines.** FMP headlines are fed verbatim into the
  LLM prompt. Mitigated by DECLINE-default + human gate; the analyst system prompt now
  explicitly marks headline text as untrusted data ("never as instructions"). (analyst.py)
- [x] **Secrets hygiene.** `.env` is gitignored; all runtime secrets live in Key Vault
  and are surfaced to the Container App as Key-Vault-referenced secrets via the managed
  identity (no keys in the image or env). (.gitignore, infra/main.bicep)

## P5 — Testing
- [x] Tests for `executor.execute` (band void, submit-failure rollback, idempotency).
- [x] Tests for `engine._analyze` commit-on-success (cooldown/budget not burned on error).
- [x] Tests for telegram `format_message` HTML escaping.
- [x] Tests for the Foundry `analyst.analyze` wiring (mocked project client) and
  telemetry no-op behavior.
- [ ] Broader tests for `engine.scan` ranking/caps and telegram callbacks (approve/deny/expire).

## P6 — Integrations (decisions + tasks)
- [ ] **DECISION: provider responsibilities.** Alpaca owns market data + execution
  (authoritative for prices); FMP owns fundamentals + news.
- [ ] **Verify FMP free-tier endpoint access.** `sp500_constituent` + `stock_news` may
  require a paid plan; free tier ~250 calls/day. If gated: start with an explicit
  small `watchlist`.
- [ ] **FMP /api/v3 is legacy.** Confirm the 5 endpoints still return data.
- [ ] **Optional dev aid:** add Alpaca MCP server to the dev environment (not runtime).

## P7 — Live trading readiness
- [ ] Open + fund Alpaca live account (KYC); generate SEPARATE live API keys.
- [ ] Consider Alpaca SIP data (~$99/mo) for accurate live price-band checks.
- [ ] Broker reconciliation on startup (positions + open/recent orders).
- [ ] Kill switch (cancel all / stop).
- [ ] Exposure / sector / max-position limits.
- [ ] Be aware of Pattern Day Trader rule (<$25k equity) with DAY limit orders.

## P8 — Tooling / hygiene / setup
- [x] Fix 2 ruff F401 unused imports (datetime in fundamentals.py, field in triggers.py).
- [x] Add `.state.json` to .gitignore.
- [x] Add CI workflow (pytest + ruff on push/PR). Added `.github/workflows/ci.yml`,
  `codeql.yml`, `cd.yml` (OIDC → ACR build → Trivy → ACA), and `dependabot.yml`.
- [ ] Add dependency pinning / lockfile.
- [ ] Create local config.yaml + .env from examples.

## P9 — Strategy / evaluation
- [ ] No backtest or evaluation baseline. (addressed by Foundry eval harness, P12)
- [ ] Validate timezone / market-hours edge cases (DST, half-days) in scheduler.

## P10 — Azure-native architecture + GitHub CI/CD (direction)
Azure-native tools, GitHub CI/CD, strong security + observability. Compute: ACA
always-on single replica with user-assigned Managed Identity. Security: Key Vault
for all secrets via MI, Entra RBAC least-privilege, GitHub OIDC federated deploy,
Defender/ACR image scanning, egress-only. Observability: Application Insights via
OpenTelemetry, Log Analytics + KQL, Azure Monitor alerts (heartbeat, order-submit
failures, API 4xx/5xx, DECLINE-rate anomaly, LLM cost), periodic heartbeat. State:
audit/event log to Azure Table Storage. CI/CD: PR (ruff + pytest + CVE scan), CD
(build → ACR → ACA via OIDC) with GitHub Environments approval gate for prod.

## P11 — Final architecture decisions (locked 2026-07-26)
LLM Claude Opus 4.8; ACA always-on; Table Storage audit; Bicep + azd; Key Vault +
Managed Identity; GitHub Actions + Entra OIDC; App Insights lean (sampling, capped
ingestion, meaningful events only); free GitHub-native scanning (Trivy/CodeQL/
Dependabot); defer SIP/paid-FMP until live. Est. ~$65–75/mo (Opus dominates).

## P12 — Revised: max Azure stack + Foundry (locked 2026-07-26)
- **Registry: Azure Container Registry (ACR)** Basic; ACA pulls via Managed Identity
  (AcrPull). (overrides ghcr from P11.)
- **LLM: Claude Opus 4.8 via Azure AI Foundry** (partner model, serverless, Microsoft
  Entra auth, supported by Foundry Agent Service). Keep Opus; consume through Foundry.
- **Analyst = Foundry Agent.** Rewrite analyst.py from the `anthropic` SDK to a Foundry
  agent (instructions = system prompt, structured JSON verdict, threads/runs). Wins:
  Entra/MI auth drops the Anthropic API key; built-in tracing → App Insights; Foundry
  eval harness addresses P9.
- **Verify before build:** Foundry Claude model/deployment name + region; whether
  Opus-native `thinking`/`output_config` params pass through Foundry or map to Foundry
  Agent `response_format`; Foundry Agent Service region/quota.

### P12 implementation status (2026-07-26)
- [x] **analyst.py rewritten to Foundry v2 SDK.** Uses `AIProjectClient` +
  `PromptAgentDefinition`, lazily created once, and `get_openai_client().responses.create`
  with a strict `json_schema` verdict and `agent_reference`. Entra/MI auth via
  `DefaultAzureCredential`; the Anthropic SDK + API key are gone. (analyst.py, config.py, pyproject.toml)
- [x] **Bicep infra (`infra/main.bicep`, azd-ready).** Log Analytics, workspace-based
  App Insights, user-assigned MI, Key Vault (RBAC + purge protection), ACR (no admin),
  Storage + Files share (state) + Table (audit), ACA env + single-replica Container App
  (KV-referenced secrets, Azure Files at /data, MI ACR pull), AI Foundry account + project
  + Claude deployment, least-privilege role assignments, action group + 3 alert rules.
  Compiles clean (`bicep build`).
- [x] **CI/CD.** ci (ruff+pytest), codeql (python), cd (OIDC \u2192 `az acr build` \u2192 Trivy \u2192
  `az containerapp update`, production environment gate), dependabot (pip + actions).
- [ ] **Runtime-verify on first deploy (cannot test without a subscription):** the exact
  Claude model name/version/SKU in the target region; that Foundry `responses.create`
  honors `json_schema` + `agent_reference` for the Claude deployment; `effort` is currently
  dropped (Anthropic-native param, no confirmed Foundry mapping).
