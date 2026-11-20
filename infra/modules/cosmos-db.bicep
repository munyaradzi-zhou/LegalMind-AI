// ---------------------------------------------------------------------------
// Module: cosmos-db.bicep
// Azure Cosmos DB (NoSQL API) for session and memory persistence
// ---------------------------------------------------------------------------

@description('Azure region for the Cosmos DB account.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Principal ID of the Container App managed identity to grant Cosmos DB data access.')
param containerAppPrincipalId string = ''

@description('Days a conversation (and long-term memory) is kept after its last message.')
param chatRetentionDays int = 30

@description('Days an audit event is kept (it holds no message text, see services/audit_logger.py).')
param auditRetentionDays int = 365

@description('Enabled, or Disabled when the account is only reachable through its private endpoint.')
param publicNetworkAccess string = 'Enabled'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Short hash of the resource group: these names must be globally unique in Azure.
var suffix = take(uniqueString(resourceGroup().id), 5)
var cosmosAccountName = 'cosmos-${projectName}-${environmentName}-${suffix}'
var databaseName = 'legalmind'

// Built-in role: Cosmos DB Built-in Data Contributor
// This is the Cosmos DB data-plane RBAC role ID (not ARM)
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// ---------------------------------------------------------------------------
// Cosmos DB Account
// ---------------------------------------------------------------------------
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-02-15-preview' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: environmentName == 'prod'
      }
    ]
    capabilities: environmentName == 'prod' ? [] : [
      {
        name: 'EnableServerless'
      }
    ]
    enableAutomaticFailover: environmentName == 'prod'
    enableMultipleWriteLocations: false
    publicNetworkAccess: publicNetworkAccess
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
  }
}

// ---------------------------------------------------------------------------
// Database: legalmind
// ---------------------------------------------------------------------------
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-02-15-preview' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// ---------------------------------------------------------------------------
// Container: sessions (partitioned by /id)
// ---------------------------------------------------------------------------
resource sessionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: database
  name: 'sessions'
  properties: {
    resource: {
      id: 'sessions'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: chatRetentionDays * 86400 // seconds; Cosmos deletes expired items itself
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container: memory (partitioned by /id)
// ---------------------------------------------------------------------------
resource memoryContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: database
  name: 'memory'
  properties: {
    resource: {
      id: 'memory'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: chatRetentionDays * 86400
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container: audit_log (partitioned by /id) — written by services/audit_logger.py
// ---------------------------------------------------------------------------
resource auditLogContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: database
  name: 'audit_log'
  properties: {
    resource: {
      id: 'audit_log'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: auditRetentionDays * 86400
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: Grant Container App managed identity Cosmos DB data-plane access
// ---------------------------------------------------------------------------
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-02-15-preview' = if (!empty(containerAppPrincipalId)) {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, containerAppPrincipalId, cosmosDataContributorRoleId)
  properties: {
    principalId: containerAppPrincipalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    scope: cosmosAccount.id
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Cosmos DB account.')
output cosmosAccountId string = cosmosAccount.id

@description('Name of the Cosmos DB account.')
output cosmosAccountName string = cosmosAccount.name

@description('Cosmos DB account endpoint.')
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint

@description('Name of the Cosmos DB database.')
output databaseName string = database.name
