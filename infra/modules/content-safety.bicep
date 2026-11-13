// ─── Azure AI Content Safety ────────────────────────────────────────────────
// Deploys an Azure AI Content Safety resource for input/output moderation
// and harmful content detection in the LegalMind AI platform.

@description('Azure region for the resource')
param location string

@description('Environment name (dev, staging, prod)')
param environmentName string

@description('Project name prefix for resource naming')
param projectName string

@description('Principal ID of the Container App managed identity for RBAC')
param containerAppPrincipalId string = ''

// ─── Variables ──────────────────────────────────────────────────────────────

var tags = {
  project: 'legalmind'
  environment: environmentName
  component: 'content-safety'
}

// Short hash of the resource group: these names must be globally unique in Azure.
var suffix = take(uniqueString(resourceGroup().id), 5)
var contentSafetyName = 'csafety-${projectName}-${environmentName}-${suffix}'

// Cognitive Services User role — allows calling the Content Safety API
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// ─── Content Safety Resource ────────────────────────────────────────────────

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: contentSafetyName
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: contentSafetyName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true  // Enforce managed identity auth only
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ─── RBAC: Grant Container App access ───────────────────────────────────────

resource contentSafetyRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(contentSafety.id, containerAppPrincipalId, cognitiveServicesUserRoleId)
  scope: contentSafety
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: containerAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Outputs ────────────────────────────────────────────────────────────────

output contentSafetyId string = contentSafety.id
output contentSafetyName string = contentSafety.name
output contentSafetyEndpoint string = contentSafety.properties.endpoint
