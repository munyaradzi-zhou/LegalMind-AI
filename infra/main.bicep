// =============================================================================
// main.bicep — LegalMind AI platform infrastructure
//
// Order: identity → services (each grants the app identity its data-plane role)
//        → container registry → Container Apps (API + frontend) → API Management.
// All service-to-service access uses Entra ID; no keys or connection strings
// (except the Application Insights connection string, which is not a secret).
// =============================================================================

targetScope = 'resourceGroup'

// =============================================================================
// Parameters
// =============================================================================

@description('Deployment environment name. Determines SKUs, scaling and redundancy.')
@allowed(['dev', 'staging', 'prod'])
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Region for Azure AI Search. Defaults to location; set it when that region has no Search capacity.')
param searchLocation string = location

@description('Short project name used in resource names. Lowercase alphanumeric.')
@minLength(3)
@maxLength(12)
param projectName string = 'legalmind'

@description('Publisher email for the API Management instance.')
param apimPublisherEmail string

@description('Publisher organization name for the API Management instance.')
param apimPublisherName string = 'LegalMind AI'

@description('API image tag in the registry (e.g. the commit SHA). Empty = placeholder image (first deploy).')
param apiImageTag string = ''

@description('Frontend image tag in the registry. Empty = placeholder image (first deploy).')
param frontendImageTag string = ''

// =============================================================================
// Identity used by both Container Apps (created first so roles can be granted up front)
// =============================================================================
module identity 'modules/identity.bicep' = {
  name: 'identity-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
  }
}

var appPrincipalId = identity.outputs.principalId

// =============================================================================
// Observability
// =============================================================================
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
  }
}

// =============================================================================
// AI and data services (each module also grants the app identity its role)
// =============================================================================
module aiFoundry 'modules/ai-foundry.bicep' = {
  name: 'ai-foundry-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module aiSearch 'modules/ai-search.bicep' = {
  name: 'ai-search-${environmentName}'
  params: {
    location: searchLocation
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module cognitiveServices 'modules/cognitive-services.bicep' = {
  name: 'cognitive-services-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module contentUnderstanding 'modules/content-understanding.bicep' = {
  name: 'content-understanding-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module contentSafety 'modules/content-safety.bicep' = {
  name: 'content-safety-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    containerAppPrincipalId: appPrincipalId
  }
}

// =============================================================================
// Container registry and Container Apps
// =============================================================================
module registry 'modules/container-registry.bicep' = {
  name: 'container-registry-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    pullPrincipalId: appPrincipalId
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    identityId: identity.outputs.identityId
    identityClientId: identity.outputs.clientId
    acrLoginServer: registry.outputs.loginServer
    apiImage: empty(apiImageTag) ? '' : '${registry.outputs.loginServer}/legalmind-api:${apiImageTag}'
    frontendImage: empty(frontendImageTag) ? '' : '${registry.outputs.loginServer}/legalmind-frontend:${frontendImageTag}'
    openAiEndpoint: aiFoundry.outputs.openAiEndpoint
    chatDeployment: aiFoundry.outputs.chatDeploymentName
    embeddingDeployment: aiFoundry.outputs.embeddingDeploymentName
    searchEndpoint: aiSearch.outputs.searchEndpoint
    aiServicesEndpoint: cognitiveServices.outputs.aiServicesEndpoint
    documentIntelligenceEndpoint: cognitiveServices.outputs.documentIntelligenceEndpoint
    contentUnderstandingEndpoint: contentUnderstanding.outputs.contentUnderstandingEndpoint
    contentSafetyEndpoint: contentSafety.outputs.contentSafetyEndpoint
    cosmosEndpoint: cosmosDb.outputs.cosmosEndpoint
    blobEndpoint: storage.outputs.blobEndpoint
    keyVaultUri: keyVault.outputs.keyVaultUri
  }
}

// =============================================================================
// API Management (Consumption tier) in front of the API
// =============================================================================
module apiManagement 'modules/api-management.bicep' = {
  name: 'api-management-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    backendUrl: containerApps.outputs.apiUrl
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
  }
}

// =============================================================================
// Outputs (consumed by scripts/deploy.sh and the CD workflow)
// =============================================================================
@description('Container registry name.')
output registryName string = registry.outputs.registryName

@description('Container registry login server.')
output registryLoginServer string = registry.outputs.loginServer

@description('API Container App name.')
output apiAppName string = containerApps.outputs.apiAppName

@description('Frontend Container App name.')
output frontendAppName string = containerApps.outputs.frontendAppName

@description('API URL.')
output apiUrl string = containerApps.outputs.apiUrl

@description('Frontend URL.')
output frontendUrl string = containerApps.outputs.frontendUrl

@description('API Management gateway URL.')
output apimGatewayUrl string = apiManagement.outputs.apimGatewayUrl

@description('Azure OpenAI endpoint.')
output openAiEndpoint string = aiFoundry.outputs.openAiEndpoint

@description('Azure AI Search endpoint.')
output searchEndpoint string = aiSearch.outputs.searchEndpoint

@description('Azure AI Services endpoint (Language).')
output aiServicesEndpoint string = cognitiveServices.outputs.aiServicesEndpoint

@description('Document Intelligence endpoint.')
output documentIntelligenceEndpoint string = cognitiveServices.outputs.documentIntelligenceEndpoint

@description('Content Understanding endpoint.')
output contentUnderstandingEndpoint string = contentUnderstanding.outputs.contentUnderstandingEndpoint

@description('Content Safety endpoint.')
output contentSafetyEndpoint string = contentSafety.outputs.contentSafetyEndpoint

@description('Cosmos DB endpoint.')
output cosmosEndpoint string = cosmosDb.outputs.cosmosEndpoint

@description('Blob Storage endpoint.')
output blobEndpoint string = storage.outputs.blobEndpoint

@description('Key Vault URI.')
output keyVaultUri string = keyVault.outputs.keyVaultUri

@description('Application Insights connection string (for local runs against Azure).')
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
