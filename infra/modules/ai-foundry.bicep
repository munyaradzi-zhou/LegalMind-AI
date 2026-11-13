// ---------------------------------------------------------------------------
// Module: ai-foundry.bicep
// Azure OpenAI (AI Foundry) — GPT-4o & text-embedding-3-large deployments
// ---------------------------------------------------------------------------

@description('Azure region for the Azure OpenAI resource.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the Container App managed identity to grant Cognitive Services OpenAI User role.')
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
var openAiName = 'oai-${projectName}-${environmentName}-${suffix}'

// Built-in role: Cognitive Services OpenAI User
var cognitiveServicesOpenAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

// Capacity by environment (tokens-per-minute in thousands)
var chatCapacity = environmentName == 'prod' ? 80 : (environmentName == 'staging' ? 40 : 30)
var embeddingCapacity = environmentName == 'prod' ? 120 : (environmentName == 'staging' ? 60 : 10)

// ---------------------------------------------------------------------------
// Azure OpenAI Resource
// ---------------------------------------------------------------------------
resource openAi 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: openAiName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ---------------------------------------------------------------------------
// Model Deployment: gpt-4.1-mini (chat, routing, rewriting, guardrail checks)
// (gpt-4o-mini 2024-07-18 can no longer be deployed: deprecated 2026-03-31)
// ---------------------------------------------------------------------------
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAi
  name: 'gpt-4.1-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

// ---------------------------------------------------------------------------
// Model Deployment: text-embedding-3-large
// ---------------------------------------------------------------------------
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAi
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
  }
  dependsOn: [
    chatDeployment // Deploy sequentially to avoid throttling
  ]
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App managed identity "Cognitive Services OpenAI User"
// ---------------------------------------------------------------------------
resource openAiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(openAi.id, containerAppPrincipalId, cognitiveServicesOpenAiUserRoleId)
  scope: openAi
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Azure OpenAI resource.')
output openAiId string = openAi.id

@description('Name of the Azure OpenAI resource.')
output openAiName string = openAi.name

@description('Azure OpenAI endpoint.')
output openAiEndpoint string = openAi.properties.endpoint

@description('Chat model deployment name.')
output chatDeploymentName string = chatDeployment.name

@description('Text-embedding-3-large deployment name.')
output embeddingDeploymentName string = embeddingDeployment.name
