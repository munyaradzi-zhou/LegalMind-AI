// ---------------------------------------------------------------------------
// Module: semantic-cache.bicep
// Azure Managed Redis with RediSearch: the vector store behind the AI gateway's
// semantic cache (api-management.bicep). Optional and off by default: it costs
// about €1-2 a day, and is meant to be measured, then removed.
// ---------------------------------------------------------------------------

@description('Azure region (next to API Management).')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

var suffix = take(uniqueString(resourceGroup().id), 5)

resource redis 'Microsoft.Cache/redisEnterprise@2025-04-01' = {
  name: 'redis-${projectName}-${environmentName}-${suffix}'
  location: location
  tags: {
    project: 'legalmind'
    environment: environmentName
  }
  sku: {
    name: 'Balanced_B0' // the smallest size; enough for a cache of answers
  }
  properties: {
    minimumTlsVersion: '1.2'
  }
}

resource database 'Microsoft.Cache/redisEnterprise/databases@2025-04-01' = {
  parent: redis
  name: 'default'
  properties: {
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'EnterpriseCluster' // RediSearch needs it
    evictionPolicy: 'NoEviction' // and this
    modules: [{ name: 'RediSearch' }]
    accessKeysAuthentication: 'Enabled' // APIM's external cache connects with a key
  }
}

output redisName string = redis.name
