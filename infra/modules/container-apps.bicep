// ---------------------------------------------------------------------------
// Module: container-apps.bicep
// Container Apps Environment + FastAPI backend + Streamlit frontend.
// Both apps run as the shared user-assigned identity (see identity.bicep).
// ---------------------------------------------------------------------------

@description('Azure region for the Container Apps resources.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Customer ID of the Log Analytics workspace.')
param logAnalyticsCustomerId string

@description('Resource ID of the Log Analytics workspace (used to read its shared key).')
param logAnalyticsId string

@description('Application Insights connection string for app telemetry.')
param appInsightsConnectionString string

@description('Resource ID of the user-assigned identity both apps run as.')
param identityId string

@description('Client ID of that identity (AZURE_CLIENT_ID for DefaultAzureCredential).')
param identityClientId string

@description('ACR login server the images are pulled from.')
param acrLoginServer string

@description('API image (e.g. <acr>/legalmind-api:<tag>). Empty = placeholder image until the first app deploy.')
param apiImage string = ''

@description('Frontend image (e.g. <acr>/legalmind-frontend:<tag>). Empty = placeholder image.')
param frontendImage string = ''

@description('Azure OpenAI endpoint.')
param openAiEndpoint string

@description('Azure OpenAI chat deployment name.')
param chatDeployment string

@description('Azure OpenAI embedding deployment name.')
param embeddingDeployment string

@description('Azure AI Search endpoint.')
param searchEndpoint string

@description('Azure AI Services endpoint (used for Language: NER, PII, key phrases).')
param aiServicesEndpoint string

@description('Document Intelligence endpoint.')
param documentIntelligenceEndpoint string

@description('Content Understanding endpoint.')
param contentUnderstandingEndpoint string

@description('Content Safety endpoint.')
param contentSafetyEndpoint string

@description('Cosmos DB endpoint.')
param cosmosEndpoint string

@description('Blob Storage endpoint.')
param blobEndpoint string

@description('Key Vault URI.')
param keyVaultUri string

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

var isProd = environmentName == 'prod'
var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest' // listens on port 80

// The placeholder only listens on port 80 and has no /health endpoint.
var apiUsesPlaceholder = empty(apiImage)
var frontendUsesPlaceholder = empty(frontendImage)

var healthProbes = [
  {
    type: 'Startup'
    httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
    initialDelaySeconds: 5
    periodSeconds: 5
    failureThreshold: 24 // up to ~2 minutes for cold start
  }
  {
    type: 'Liveness'
    httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
    periodSeconds: 30
    failureThreshold: 3
  }
  {
    type: 'Readiness'
    httpGet: { path: '/health', port: 8000, scheme: 'HTTP' }
    periodSeconds: 10
    failureThreshold: 3
  }
]

// ---------------------------------------------------------------------------
// Container Apps Environment
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsId, '/'))
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${projectName}-${environmentName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    zoneRedundant: isProd
  }
}

// ---------------------------------------------------------------------------
// Container App — FastAPI backend
// ---------------------------------------------------------------------------
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${projectName}-api-${environmentName}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: apiUsesPlaceholder ? 80 : 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        { server: acrLoginServer, identity: identityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'legalmind-api'
          image: apiUsesPlaceholder ? placeholderImage : apiImage
          resources: {
            cpu: json(isProd ? '1.0' : '0.5')
            memory: isProd ? '2Gi' : '1Gi'
          }
          // Names must match the settings in src/legalmind/config.py.
          env: [
            { name: 'APP_ENV', value: isProd ? 'production' : environmentName }
            { name: 'LLM_PROVIDER', value: 'azure' }
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: chatDeployment }
            { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
            { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'AZURE_LANGUAGE_ENDPOINT', value: aiServicesEndpoint }
            { name: 'AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT', value: documentIntelligenceEndpoint }
            { name: 'AZURE_CONTENT_UNDERSTANDING_ENDPOINT', value: contentUnderstandingEndpoint }
            { name: 'AZURE_CONTENT_SAFETY_ENDPOINT', value: contentSafetyEndpoint }
            { name: 'AZURE_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: blobEndpoint }
            { name: 'AZURE_KEYVAULT_URL', value: keyVaultUri }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
          probes: apiUsesPlaceholder ? [] : healthProbes
        }
      ]
      scale: {
        minReplicas: isProd ? 2 : 0
        maxReplicas: isProd ? 10 : 3
        rules: [
          { name: 'http-scaling', http: { metadata: { concurrentRequests: '50' } } }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container App — Streamlit frontend (calls the API server-side)
// ---------------------------------------------------------------------------
resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${projectName}-web-${environmentName}'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: frontendUsesPlaceholder ? 80 : 8501
        transport: 'http' // Streamlit uses WebSockets; 'http' ingress supports them
        allowInsecure: false
      }
      registries: [
        { server: acrLoginServer, identity: identityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'legalmind-web'
          image: frontendUsesPlaceholder ? placeholderImage : frontendImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'LEGALMIND_API_URL', value: 'https://${apiApp.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: {
        minReplicas: isProd ? 1 : 0
        maxReplicas: isProd ? 3 : 1 // Streamlit sessions live in memory: keep one replica outside prod
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Name of the Container Apps Environment.')
output containerAppEnvName string = containerAppEnv.name

@description('Name of the API Container App.')
output apiAppName string = apiApp.name

@description('Name of the frontend Container App.')
output frontendAppName string = frontendApp.name

@description('HTTPS URL of the API.')
output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'

@description('HTTPS URL of the frontend.')
output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'
