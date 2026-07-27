// SmartCapital infrastructure — built on Azure Verified Modules (AVM).
//
// Every resource is provisioned through a published `br/public:avm/res/...`
// module so the stack inherits AVM's security defaults, naming, diagnostics and
// least-privilege role-assignment plumbing. Two thin raw resources remain
// because AVM does not expose them: the AI Foundry *project* child (there is no
// `projects` parameter on the cognitive-services/account module) and the
// Container Apps environment Azure Files mount (needs the storage account key,
// which AVM intentionally never outputs).
//
// Shape: a single always-on Container Apps worker (no ingress, one replica) that
// authenticates to everything with a user-assigned managed identity. No account
// keys and no model API key: app secrets live in Key Vault, the LLM analyst
// reaches Azure AI Foundry with Entra auth, and the container image is pulled
// from ACR by the same identity.
//
// Deploy with azd (`azd up`) or:
//   az deployment group create -g <rg> -f infra/main.bicep -p @infra/main.parameters.json

targetScope = 'resourceGroup'

@minLength(1)
@description('Name used to derive resource names and tag resources (azd environment name).')
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Region for the Azure AI Foundry account (model availability may differ).')
param aiLocation string = location

@description('Email address that receives operational alerts.')
param alertEmail string

@description('Alpaca trading environment. Keep "paper" until the paper run behaves.')
@allowed(['paper', 'live'])
param alpacaEnv string = 'paper'

@description('Telegram chat id that receives approval prompts (not a secret).')
param telegramChatId string = ''

@description('Azure OpenAI model to deploy in Foundry. Verified in the catalog: `az cognitiveservices model list -l <region>`.')
param openaiModelName string = 'gpt-5-mini'

@description('Model version (from the catalog; gpt-5-mini offers GlobalStandard).')
param openaiModelVersion string = '2025-08-07'

@description('Deployment (alias) name the app uses to reference the model.')
param openaiDeploymentName string = 'gpt-5-mini'

@description('GlobalStandard capacity (thousands of TPM). Small footprint: a few analyses/day.')
param openaiCapacity int = 50

@description('Container image to run. Defaults to a placeholder until CI pushes the real tag.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@secure()
@description('Alpaca API key.')
param alpacaApiKey string
@secure()
@description('Alpaca secret key.')
param alpacaSecretKey string
@secure()
@description('Financial Modeling Prep API key.')
param fmpApiKey string
@secure()
@description('Telegram bot token.')
param telegramBotToken string

var token = uniqueString(subscription().id, resourceGroup().id, environmentName)
var tags = { 'azd-env-name': environmentName }
var stateShareName = 'smartcapital-data'
var envStorageName = 'statefiles'
var storageAccountName = 'st${token}'
var aiAccountName = 'aif-${token}'
var aiProjectName = 'smartcapital'

// Built-in role definition GUIDs (passed by id so name-resolution never drifts).
var kvSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var tableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var azureAiUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

// --- Observability -----------------------------------------------------------
module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.16.0' = {
  name: 'log-analytics'
  params: {
    name: 'log-${token}'
    location: location
    tags: tags
    skuName: 'PerGB2018'
    dataRetention: 30
  }
}

module appInsights 'br/public:avm/res/insights/component:0.8.0' = {
  name: 'app-insights'
  params: {
    name: 'appi-${token}'
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.resourceId
    applicationType: 'web'
    kind: 'web'
    // Let the app identity publish custom metrics for the alert rules below.
    roleAssignments: [
      {
        principalId: uami.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: monitoringMetricsPublisher
      }
    ]
  }
}

// --- Identity ----------------------------------------------------------------
module uami 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  name: 'uami'
  params: {
    name: 'id-${token}'
    location: location
    tags: tags
  }
}

// --- Secrets -----------------------------------------------------------------
module keyVault 'br/public:avm/res/key-vault/vault:0.14.0' = {
  name: 'key-vault'
  params: {
    name: 'kv-${token}'
    location: location
    tags: tags
    sku: 'standard'
    enableRbacAuthorization: true // no access policies; RBAC only
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    secrets: [
      { name: 'alpaca-api-key', value: alpacaApiKey }
      { name: 'alpaca-secret-key', value: alpacaSecretKey }
      { name: 'fmp-api-key', value: fmpApiKey }
      { name: 'telegram-bot-token', value: telegramBotToken }
    ]
    roleAssignments: [
      {
        principalId: uami.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: kvSecretsUser
      }
    ]
  }
}

// --- Registry ----------------------------------------------------------------
module acr 'br/public:avm/res/container-registry/registry:0.12.1' = {
  name: 'acr'
  params: {
    name: 'acr${token}'
    location: location
    tags: tags
    acrSku: 'Basic'
    acrAdminUserEnabled: false // MI pull only, no admin creds
    roleAssignments: [
      {
        principalId: uami.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: acrPull
      }
    ]
  }
}

// --- State + audit storage ---------------------------------------------------
module storage 'br/public:avm/res/storage/storage-account:0.33.0' = {
  name: 'storage'
  params: {
    name: storageAccountName
    location: location
    tags: tags
    skuName: 'Standard_LRS'
    kind: 'StorageV2'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // The Container Apps environment is not VNet-integrated, so it reaches the
    // Azure Files share over the public endpoint. AVM defaults networkAcls to
    // Deny, which silently blocks the CIFS mount and hangs the pod in
    // PodInitializing. Allow public network access (auth still required: account
    // key for Files, Entra for tables) since there is no VNet to scope to.
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    fileServices: {
      shares: [
        { name: stateShareName, shareQuota: 1 }
      ]
    }
    tableServices: {
      tables: [
        { name: 'audit' }
      ]
    }
    roleAssignments: [
      {
        principalId: uami.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: tableDataContributor
      }
    ]
  }
}

// Escape hatch: AVM never outputs storage keys, but the Container Apps
// environment Azure Files mount below requires one. Reference the account the
// module created to read its key at deploy time.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

// --- Azure AI Foundry (the analyst LLM) --------------------------------------
module aiAccount 'br/public:avm/res/cognitive-services/account:0.15.1' = {
  name: 'ai-foundry'
  params: {
    name: aiAccountName
    location: aiLocation
    tags: tags
    kind: 'AIServices'
    sku: 'S0'
    customSubDomainName: aiAccountName
    allowProjectManagement: true // enable AI Foundry project feature
    disableLocalAuth: true // Entra auth only, no keys
    publicNetworkAccess: 'Enabled'
    managedIdentities: { systemAssigned: true }
    // gpt-5-mini is a first-party Azure OpenAI model. Coordinates verified
    // against the live catalog: gpt-5-mini 2025-08-07 is offered as
    // GlobalStandard in eastus2 with quota on this subscription. Re-check per
    // region before switching models.
    deployments: [
      {
        name: openaiDeploymentName
        model: {
          format: 'OpenAI'
          name: openaiModelName
          version: openaiModelVersion
        }
        sku: { name: 'GlobalStandard', capacity: openaiCapacity }
      }
    ]
    roleAssignments: [
      {
        principalId: uami.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: azureAiUser
      }
    ]
  }
}

// AI Foundry project — no AVM parameter exists for the project child resource,
// so create it directly against the account the module provisioned.
resource aiAccountExisting 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: aiAccountName
}

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: aiAccountExisting
  name: aiProjectName
  location: aiLocation
  identity: { type: 'SystemAssigned' }
  properties: {}
  dependsOn: [ aiAccount ]
}

// --- Container Apps ----------------------------------------------------------
module acaEnv 'br/public:avm/res/app/managed-environment:0.14.0' = {
  name: 'aca-env'
  params: {
    name: 'cae-${token}'
    location: location
    tags: tags
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsWorkspaceResourceId: logAnalytics.outputs.resourceId
    }
    zoneRedundant: false // single replica, single AZ — no benefit, avoids VNet needs
  }
}

// Azure Files mount for the environment (raw child: needs the storage key).
resource acaEnvStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  name: 'cae-${token}/${envStorageName}'
  properties: {
    azureFile: {
      accountName: storageAccountName
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: stateShareName
      accessMode: 'ReadWrite'
    }
  }
  dependsOn: [ storage, acaEnv ]
}

var kvUri = keyVault.outputs.uri

module app 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'container-app'
  params: {
    name: 'ca-${token}'
    location: location
    tags: union(tags, { 'azd-service-name': 'smartcapital' })
    environmentResourceId: acaEnv.outputs.resourceId
    managedIdentities: {
      userAssignedResourceIds: [ uami.outputs.resourceId ]
    }
    activeRevisionsMode: 'Single'
    disableIngress: true // worker, not a web service
    scaleSettings: {
      minReplicas: 1
      maxReplicas: 1 // never scale out: one human, one queue
    }
    registries: [
      {
        server: acr.outputs.loginServer
        identity: uami.outputs.resourceId
      }
    ]
    secrets: [
      { name: 'alpaca-api-key', keyVaultUrl: '${kvUri}secrets/alpaca-api-key', identity: uami.outputs.resourceId }
      { name: 'alpaca-secret-key', keyVaultUrl: '${kvUri}secrets/alpaca-secret-key', identity: uami.outputs.resourceId }
      { name: 'fmp-api-key', keyVaultUrl: '${kvUri}secrets/fmp-api-key', identity: uami.outputs.resourceId }
      { name: 'telegram-bot-token', keyVaultUrl: '${kvUri}secrets/telegram-bot-token', identity: uami.outputs.resourceId }
    ]
    volumes: [
      { name: 'data', storageType: 'AzureFile', storageName: envStorageName }
    ]
    containers: [
      {
        name: 'smartcapital'
        image: containerImage
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        volumeMounts: [
          { volumeName: 'data', mountPath: '/data' }
        ]
        env: [
          { name: 'ALPACA_ENV', value: alpacaEnv }
          { name: 'ALPACA_API_KEY', secretRef: 'alpaca-api-key' }
          { name: 'ALPACA_SECRET_KEY', secretRef: 'alpaca-secret-key' }
          { name: 'FMP_API_KEY', secretRef: 'fmp-api-key' }
          { name: 'TELEGRAM_BOT_TOKEN', secretRef: 'telegram-bot-token' }
          { name: 'TELEGRAM_CHAT_ID', value: telegramChatId }
          { name: 'STATE_FILE', value: '/data/state.json' }
          { name: 'AZURE_CLIENT_ID', value: uami.outputs.clientId } // pin DefaultAzureCredential to the UAMI
          { name: 'AZURE_AI_PROJECT_ENDPOINT', value: 'https://${aiAccountName}.services.ai.azure.com/api/projects/${aiProjectName}' }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.outputs.connectionString }
        ]
      }
    ]
  }
  dependsOn: [ acaEnvStorage ]
}

// --- Alerting ----------------------------------------------------------------
module actionGroup 'br/public:avm/res/insights/action-group:0.8.0' = {
  name: 'action-group'
  params: {
    name: 'ag-${token}'
    groupShortName: 'smartcap'
    location: 'global'
    tags: tags
    emailReceivers: [
      { name: 'ops', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

// Liveness: the app emits smartcapital.heartbeat every minute. Alert if none
// arrived in the last 15 minutes.
module alertHeartbeat 'br/public:avm/res/insights/scheduled-query-rule:0.6.0' = {
  name: 'alert-heartbeat'
  params: {
    name: 'alert-heartbeat-${token}'
    location: location
    tags: tags
    scopes: [ appInsights.outputs.resourceId ]
    severity: 1
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criterias: {
      allOf: [
        {
          query: 'customMetrics | where name == "smartcapital.heartbeat"'
          timeAggregation: 'Count'
          operator: 'LessThanOrEqual'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: true
    actions: { actionGroupResourceIds: [ actionGroup.outputs.resourceId ] }
  }
}

module alertOrderFailures 'br/public:avm/res/insights/scheduled-query-rule:0.6.0' = {
  name: 'alert-orderfail'
  params: {
    name: 'alert-orderfail-${token}'
    location: location
    tags: tags
    scopes: [ appInsights.outputs.resourceId ]
    severity: 1
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criterias: {
      allOf: [
        {
          query: 'customMetrics | where name == "smartcapital.orders.failed" | summarize total = sum(value)'
          timeAggregation: 'Total'
          metricMeasureColumn: 'total'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: true
    actions: { actionGroupResourceIds: [ actionGroup.outputs.resourceId ] }
  }
}

module alertLlmCost 'br/public:avm/res/insights/scheduled-query-rule:0.6.0' = {
  name: 'alert-llmcost'
  params: {
    name: 'alert-llmcost-${token}'
    location: location
    tags: tags
    scopes: [ appInsights.outputs.resourceId ]
    severity: 2
    evaluationFrequency: 'PT1H'
    windowSize: 'P1D'
    criterias: {
      allOf: [
        {
          query: 'customMetrics | where name == "smartcapital.llm.cost_usd" | summarize usd = sum(value)'
          timeAggregation: 'Total'
          metricMeasureColumn: 'usd'
          operator: 'GreaterThan'
          threshold: 5
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    autoMitigate: true
    actions: { actionGroupResourceIds: [ actionGroup.outputs.resourceId ] }
  }
}

// --- Outputs -----------------------------------------------------------------
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.name
output AZURE_CONTAINER_APP_NAME string = app.outputs.name
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_AI_PROJECT_ENDPOINT string = 'https://${aiAccountName}.services.ai.azure.com/api/projects/${aiProjectName}'
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.outputs.connectionString
output AZURE_CLIENT_ID string = uami.outputs.clientId
