// ---------------------------------------------------------------------------
// Module: gateway-access.bicep
// The AI gateway (API Management's managed identity) calls the model accounts:
// Cognitive Services OpenAI User on each one.
// ---------------------------------------------------------------------------

@description('Principal ID of the API Management managed identity.')
param principalId string

@description('Names of the Azure OpenAI accounts in this resource group.')
param openAiNames array

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource accounts 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = [
  for name in openAiNames: {
    name: name
  }
]

resource roles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, i) in openAiNames: {
    name: guid(accounts[i].id, principalId, openAiUserRoleId)
    scope: accounts[i]
    properties: {
      principalId: principalId
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
      principalType: 'ServicePrincipal'
    }
  }
]
