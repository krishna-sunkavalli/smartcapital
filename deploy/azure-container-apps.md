# Deploying SmartCapital to Azure Container Apps

The app is a single long-running worker: it polls market data on a schedule and
long-polls Telegram for your taps. That shape maps to ACA as:

- **No ingress** — all traffic is outbound (Alpaca, FMP, Azure AI Foundry, Telegram).
- **Exactly one replica** (`min = max = 1`). Never scale this out: two replicas
  would double-analyze, double-ping, and fight over the Telegram updates queue.
  And no scale-to-zero — the scheduler must be alive during market hours.
- **Azure Files volume at `/data`** — the state file (cooldowns + daily analysis
  budget) survives container restarts and revision swaps.
- **Secrets from Key Vault**, referenced by the app's user-assigned managed
  identity. No account keys and no model API key live in the image or env.

Cost: a 0.25 vCPU / 0.5 GiB always-on app is a few dollars a month; the Claude
analyst dominates the bill.

## Recommended: infrastructure as code (`infra/main.bicep` via azd)

Everything below is provisioned by [`infra/main.bicep`](../infra/main.bicep):
Log Analytics + Application Insights, a user-assigned managed identity, Key Vault
(RBAC + purge protection), ACR (no admin user), a Storage account with a Files
share (state) and a Table (audit), the Container Apps environment + Container App,
an Azure AI Foundry account/project with a Claude deployment, least-privilege role
assignments, and Azure Monitor alerts (heartbeat-missing, order-submit failures,
daily LLM cost).

```bash
# One tool, whole stack. Prompts for env name, region, and the secure params
# (Alpaca/FMP/Telegram) which are stored in Key Vault.
azd up
```

`azd` builds the image from the [`Dockerfile`](../Dockerfile), pushes it to ACR,
deploys the Bicep, and rolls the Container App. Re-run `azd deploy` for code-only
changes or `azd provision` for infra-only changes.

Or deploy the Bicep directly:

```bash
az group create -n smartcapital-rg -l eastus
az deployment group create -g smartcapital-rg \
  -f infra/main.bicep \
  -p environmentName=smartcapital alertEmail=you@example.com \
     telegramChatId=YOUR_CHAT_ID \
     alpacaApiKey=... alpacaSecretKey=... fmpApiKey=... telegramBotToken=...
```

## How auth works (no keys at runtime)

- **Alpaca / FMP / Telegram** — stored as Key Vault secrets; the Container App
  references them via the managed identity (`Key Vault Secrets User`).
- **Azure AI Foundry (the analyst)** — Entra only. `DefaultAzureCredential` picks
  up the managed identity (the deploy injects `AZURE_CLIENT_ID`), which holds the
  `Azure AI User` role on the Foundry account. The project endpoint is injected as
  `AZURE_AI_PROJECT_ENDPOINT`.
- **ACR pulls** — the same identity with `AcrPull`; admin user is disabled.

Locally, `az login` supplies the same credential, so the analyst works on your
machine without any changes.

## CI/CD (GitHub Actions, OIDC — no stored cloud creds)

- `.github/workflows/ci.yml` — ruff + pytest on every push/PR.
- `.github/workflows/codeql.yml` — CodeQL for Python.
- `.github/workflows/cd.yml` — federated `azure/login`, `az acr build`, a Trivy
  image scan (fails on CRITICAL/HIGH), then `az containerapp update`. A
  `production` GitHub Environment provides the human approval gate.
- `.github/dependabot.yml` — weekly pip + actions updates.

Set repo **variables** `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_REGISTRY_NAME`,
`AZURE_CONTAINER_APP_NAME` and **secrets** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID` (federated credential for the deploy identity).

## Config

The image ships `config.example.yaml` (full S&P 500 scan, default caps) and falls
back to it when no `config.yaml` exists. To customize without rebuilding, put a
`config.yaml` on the file share and set `SMARTCAPITAL_CONFIG=/data/config.yaml`.
Leave `llm.project_endpoint` blank to use the injected `AZURE_AI_PROJECT_ENDPOINT`.

## Watching it

```bash
az containerapp logs show -n <app> -g smartcapital-rg --follow
```

Telemetry (heartbeat, proposals, order submitted/failed, LLM tokens/cost) flows to
Application Insights; the Bicep alerts page you if the heartbeat stops, an order
submit fails, or daily LLM spend crosses the threshold.

Go-live checklist: keep `ALPACA_ENV=paper` until the paper run has behaved for a
while; flipping to `live` is a deliberate parameter change, never a default.
