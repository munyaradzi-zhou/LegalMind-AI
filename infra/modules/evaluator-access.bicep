// ---------------------------------------------------------------------------
// Module: evaluator-access.bicep
// Data-plane roles for the CI identity that runs the evaluation gate
// (.github/workflows/eval.yml): it starts the pull request's API on the runner
// against this environment's services, so it needs what the app needs to answer
// questions — and no more (no Storage, no Key Vault, Search read-only).
// ---------------------------------------------------------------------------

@description('Object ID of the CI service principal.')
param principalId string

param openAiName string
param searchServiceName string
param aiServicesName string
param contentSafetyName string
param cosmosAccountName string

var cognitiveServicesOpenAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002' // sessions and audit log

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: openAiName }
resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' existing = { name: searchServiceName }
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: aiServicesName }
resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: contentSafetyName }
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = { name: cosmosAccountName }

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, principalId, cognitiveServicesOpenAiUserRoleId)
  scope: openAi
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
  }
}

resource searchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, searchIndexDataReaderRoleId)
  scope: searchService
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}

resource languageUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, principalId, cognitiveServicesUserRoleId)
  scope: aiServices
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

resource contentSafetyUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(contentSafety.id, principalId, cognitiveServicesUserRoleId)
  scope: contentSafety
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

resource cosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, principalId, cosmosDataContributorRoleId)
  properties: {
    principalId: principalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    scope: cosmosAccount.id
  }
}
