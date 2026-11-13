// ---------------------------------------------------------------------------
// Module: content-understanding.bicep
// Azure Content Understanding (part of Azure AI Services)
// ---------------------------------------------------------------------------

@description('Azure region for the Content Understanding resource.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the Container App managed identity to grant Cognitive Services User role.')
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
var contentUnderstandingName = 'cu-${projectName}-${environmentName}-${suffix}'

// Built-in role: Cognitive Services User
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// ---------------------------------------------------------------------------
// Content Understanding Resource (Azure AI Services kind)
// Content Understanding is deployed as an AI Services resource with a
// custom sub-domain; the service routes to the Content Understanding APIs.
// ---------------------------------------------------------------------------
resource contentUnderstanding 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: contentUnderstandingName
  location: location
  tags: tags
  kind: 'CognitiveServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: contentUnderstandingName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App identity "Cognitive Services User"
// ---------------------------------------------------------------------------
resource contentUnderstandingRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(contentUnderstanding.id, containerAppPrincipalId, cognitiveServicesUserRoleId)
  scope: contentUnderstanding
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Content Understanding resource.')
output contentUnderstandingId string = contentUnderstanding.id

@description('Name of the Content Understanding resource.')
output contentUnderstandingName string = contentUnderstanding.name

@description('Endpoint for the Content Understanding resource.')
output contentUnderstandingEndpoint string = contentUnderstanding.properties.endpoint
