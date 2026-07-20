# Deploying SmartCapital to Azure Container Apps

The app is a single long-running worker: it polls market data on a schedule and
long-polls Telegram for your taps. That shape maps to ACA as:

- **No ingress** — all traffic is outbound (Alpaca, FMP, Anthropic, Telegram).
- **Exactly one replica** (`--min-replicas 1 --max-replicas 1`). Never scale
  this out: two replicas would double-analyze, double-ping, and fight over the
  Telegram updates queue. And no scale-to-zero — the scheduler must be alive
  during market hours.
- **Azure Files volume at `/data`** — the state file (cooldowns + daily
  analysis budget) survives container restarts and revision swaps.
- **Secrets as ACA secrets**, injected as env vars.

Cost: a 0.25 vCPU / 0.5 GiB always-on app is a few dollars a month.

## One-time setup

```bash
RG=smartcapital-rg
LOC=eastus
ACR=smartcapitalacr        # must be globally unique
ENV=smartcapital-env
APP=smartcapital
SA=smartcapitalstate       # must be globally unique
SHARE=smartcapital-data

az group create -n $RG -l $LOC

# 1. Registry + build the image from the repo root
az acr create -n $ACR -g $RG --sku Basic --admin-enabled true
az acr build -r $ACR -t smartcapital:latest .

# 2. Container Apps environment
az containerapp env create -n $ENV -g $RG -l $LOC

# 3. Azure Files share for /data (state persistence)
az storage account create -n $SA -g $RG -l $LOC --sku Standard_LRS
az storage share-rm create --storage-account $SA -n $SHARE
STORAGE_KEY=$(az storage account keys list -n $SA -g $RG --query "[0].value" -o tsv)
az containerapp env storage set -n $ENV -g $RG \
  --storage-name statefiles --azure-file-account-name $SA \
  --azure-file-account-key "$STORAGE_KEY" --azure-file-share-name $SHARE \
  --access-mode ReadWrite

# 4. The app (fill in your real keys)
az containerapp create -n $APP -g $RG --environment $ENV \
  --image $ACR.azurecr.io/smartcapital:latest \
  --registry-server $ACR.azurecr.io \
  --cpu 0.25 --memory 0.5Gi \
  --min-replicas 1 --max-replicas 1 \
  --secrets alpaca-key=YOUR_KEY alpaca-secret=YOUR_SECRET \
            fmp-key=YOUR_KEY anthropic-key=YOUR_KEY \
            tg-token=YOUR_TOKEN \
  --env-vars ALPACA_ENV=paper \
             ALPACA_API_KEY=secretref:alpaca-key \
             ALPACA_SECRET_KEY=secretref:alpaca-secret \
             FMP_API_KEY=secretref:fmp-key \
             ANTHROPIC_API_KEY=secretref:anthropic-key \
             TELEGRAM_BOT_TOKEN=secretref:tg-token \
             TELEGRAM_CHAT_ID=YOUR_CHAT_ID \
             STATE_FILE=/data/state.json
```

Then attach the volume (create doesn't take mounts; patch the app definition):

```bash
az containerapp show -n $APP -g $RG -o yaml > app.yaml
# In app.yaml under properties.template add:
#   volumes:
#   - name: data
#     storageName: statefiles
#     storageType: AzureFile
# and under the container entry:
#     volumeMounts:
#     - volumeName: data
#       mountPath: /data
az containerapp update -n $APP -g $RG --yaml app.yaml
```

## Config

The image ships `config.example.yaml` (full S&P 500 scan, default caps) and
falls back to it when no `config.yaml` exists. To customize without
rebuilding, put a `config.yaml` on the file share and point at it:

```bash
az storage file upload --account-name $SA -s $SHARE --source config.yaml
az containerapp update -n $APP -g $RG --set-env-vars SMARTCAPITAL_CONFIG=/data/config.yaml
```

## Ship a new version

```bash
az acr build -r $ACR -t smartcapital:latest .
az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/smartcapital:latest
```

The revision swap restarts the process; cooldowns and the daily analysis
budget reload from `/data/state.json`, so an update mid-market-day won't
re-analyze or re-ping.

## Watching it

```bash
az containerapp logs show -n $APP -g $RG --follow
```

Go-live checklist: `ALPACA_ENV=paper` until the paper run has behaved for a
while; flipping to `live` is a deliberate env-var change, never a default.
