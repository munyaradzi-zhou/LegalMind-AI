// ---------------------------------------------------------------------------
// Module: storage.bicep
// Azure Blob Storage for legal document storage
// ---------------------------------------------------------------------------

@description('Azure region for the storage account.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the Container App managed identity to grant Storage Blob Data Contributor role.')
param containerAppPrincipalId string = ''

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Storage account names: 3-24 lowercase alphanumeric only
// Short hash of the resource group: these names must be globally unique in Azure.
var suffix = take(uniqueString(resourceGroup().id), 5)
var storageAccountName = take('st${projectName}${environmentName}${suffix}', 24)

// Built-in role: Storage Blob Data Contributor
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// ---------------------------------------------------------------------------
// Storage Account
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: environmentName == 'prod' ? 'Standard_GRS' : 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// ---------------------------------------------------------------------------
// Blob Services — enable soft delete
// ---------------------------------------------------------------------------
resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: environmentName == 'prod' ? 30 : 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: environmentName == 'prod' ? 30 : 7
    }
  }
}

// ---------------------------------------------------------------------------
// Blob Container: legal-documents
// ---------------------------------------------------------------------------
resource legalDocumentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'legal-documents'
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App managed identity "Storage Blob Data Contributor"
// ---------------------------------------------------------------------------
resource storageBlobRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerAppPrincipalId)) {
  name: guid(storageAccount.id, containerAppPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the storage account.')
output storageAccountId string = storageAccount.id

@description('Name of the storage account.')
output storageAccountName string = storageAccount.name

@description('Primary blob endpoint of the storage account.')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
