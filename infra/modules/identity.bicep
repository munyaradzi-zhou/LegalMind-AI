// ---------------------------------------------------------------------------
// Module: identity.bicep
// User-assigned managed identity shared by the API and frontend Container Apps.
// Created first so every service can grant it access before the apps start
// (a system-assigned identity only exists after its app has been created).
// ---------------------------------------------------------------------------

@description('Azure region for the identity.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${projectName}-${environmentName}'
  location: location
  tags: {
    project: 'legalmind'
    environment: environmentName
  }
}

@description('Resource ID of the identity (attached to the Container Apps).')
output identityId string = appIdentity.id

@description('Principal (object) ID of the identity, used in role assignments.')
output principalId string = appIdentity.properties.principalId

@description('Client ID of the identity (AZURE_CLIENT_ID for DefaultAzureCredential).')
output clientId string = appIdentity.properties.clientId
