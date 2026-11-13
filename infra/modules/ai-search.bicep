// ---------------------------------------------------------------------------
// Module: ai-search.bicep
// Azure AI Search — vector search + semantic ranking
// ---------------------------------------------------------------------------

@description('Azure region for the AI Search resource.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the Container App managed identity to grant Search Index Data Contributor role.')
param containerAppPrincipalId string = ''

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Short hash of the resource group: these names must be globally unique in Azure.
var suffix = take(uniqueString(resourceGroup().id), 5)
var searchServiceName = 'srch-${projectName}-${environmentName}-${suffix}'

// Built-in role: Search Index Data Contributor
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

// Built-in role: Search Service Contributor
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

// ---------------------------------------------------------------------------
// Azure AI Search Service
// ---------------------------------------------------------------------------
resource searchService 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    // Basic (~$75/month) supports semantic ranking; Standard (~$250/month) only for prod.
    name: environmentName == 'prod' ? 'standard' : 'basic'
  }
  properties: {
    hostingMode: 'default'
    partitionCount: 1
    replicaCount: environmentName == 'prod' ? 2 : 1
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: environmentName == 'prod' ? 'standard' : 'free' // free plan: 1,000 semantic queries/month
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App managed identity "Search Index Data Contributor"
// ---------------------------------------------------------------------------
resource searchIndexDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(searchService.id, containerAppPrincipalId, searchIndexDataContributorRoleId)
  scope: searchService
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App managed identity "Search Service Contributor"
// ---------------------------------------------------------------------------
resource searchServiceContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(searchService.id, containerAppPrincipalId, searchServiceContributorRoleId)
  scope: searchService
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Azure AI Search service.')
output searchServiceId string = searchService.id

@description('Name of the Azure AI Search service.')
output searchServiceName string = searchService.name

@description('Azure AI Search endpoint URL.')
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
