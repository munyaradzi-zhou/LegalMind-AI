// ---------------------------------------------------------------------------
// Module: container-registry.bicep
// Azure Container Registry for the API and frontend images. The app identity
// gets AcrPull; admin user is disabled (no registry passwords anywhere).
// ---------------------------------------------------------------------------

@description('Azure region for the registry.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the app identity that pulls images.')
param pullPrincipalId string

// Registry names must be globally unique and alphanumeric only.
var registryName = 'cr${projectName}${environmentName}${uniqueString(resourceGroup().id)}'

// Built-in role: AcrPull
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take(registryName, 50)
  location: location
  tags: {
    project: 'legalmind'
    environment: environmentName
  }
  sku: {
    name: environmentName == 'prod' ? 'Standard' : 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, pullPrincipalId, acrPullRoleId)
  properties: {
    principalId: pullPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

@description('Registry name (for az acr build / az acr login).')
output registryName string = registry.name

@description('Registry login server, e.g. crlegalmind...azurecr.io.')
output loginServer string = registry.properties.loginServer
