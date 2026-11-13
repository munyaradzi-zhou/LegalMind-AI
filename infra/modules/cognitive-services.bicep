// ---------------------------------------------------------------------------
// Module: cognitive-services.bicep
// Azure AI Services (multi-service: Language + Translator) & Document Intelligence
// ---------------------------------------------------------------------------

@description('Azure region for cognitive services resources.')
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
var aiServicesName = 'ais-${projectName}-${environmentName}-${suffix}'
var docIntelligenceName = 'di-${projectName}-${environmentName}-${suffix}'

// Built-in role: Cognitive Services User
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// ---------------------------------------------------------------------------
// Azure AI Services — multi-service account (Language, Translator, etc.)
// ---------------------------------------------------------------------------
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: aiServicesName
  location: location
  tags: tags
  kind: 'CognitiveServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ---------------------------------------------------------------------------
// Azure Document Intelligence (Form Recognizer)
// ---------------------------------------------------------------------------
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: docIntelligenceName
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: docIntelligenceName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App identity "Cognitive Services User" on AI Services
// ---------------------------------------------------------------------------
resource aiServicesRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(aiServices.id, containerAppPrincipalId, cognitiveServicesUserRoleId)
  scope: aiServices
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App identity "Cognitive Services User" on Doc Intelligence
// ---------------------------------------------------------------------------
resource docIntelligenceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(documentIntelligence.id, containerAppPrincipalId, cognitiveServicesUserRoleId)
  scope: documentIntelligence
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the multi-service AI Services account.')
output aiServicesId string = aiServices.id

@description('Name of the multi-service AI Services account.')
output aiServicesName string = aiServices.name

@description('Endpoint for the multi-service AI Services account (Language, Translator).')
output aiServicesEndpoint string = aiServices.properties.endpoint

@description('Resource ID of the Document Intelligence resource.')
output documentIntelligenceId string = documentIntelligence.id

@description('Name of the Document Intelligence resource.')
output documentIntelligenceName string = documentIntelligence.name

@description('Endpoint for the Document Intelligence resource.')
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint
