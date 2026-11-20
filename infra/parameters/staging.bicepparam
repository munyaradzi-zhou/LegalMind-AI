using '../main.bicep'

param environmentName = 'staging'
param location = 'eastus2'
// eastus2 had no AI Search capacity for new services (Nov 2026)
param searchLocation = 'centralus'
param projectName = 'legalmind'
param apimPublisherEmail = readEnvironmentVariable('APIM_PUBLISHER_EMAIL', 'admin@example.com')

// Image tags are set at deploy time (scripts/deploy.sh, CD workflow); empty = placeholder image.
param apiImageTag = readEnvironmentVariable('API_IMAGE_TAG', '')
param frontendImageTag = readEnvironmentVariable('FRONTEND_IMAGE_TAG', '')

// Remote MCP server and the deployer's Key Vault role, set by scripts/deploy.sh
param mcpEnabled = bool(readEnvironmentVariable('MCP_ENABLED', 'false'))
param deployerPrincipalId = readEnvironmentVariable('DEPLOYER_PRINCIPAL_ID', '')
param deployerPrincipalType = readEnvironmentVariable('DEPLOYER_PRINCIPAL_TYPE', 'User')

// Entra ID sign-in and matter groups, from scripts/setup_entra.sh (empty = sign-in off)
param apiAudience = readEnvironmentVariable('AZURE_API_AUDIENCE', '')
param matterGroups = readEnvironmentVariable('MATTER_GROUPS', '{}')

// Private endpoints and no public access to the data services (NETWORK_ISOLATION=true|false)
param networkIsolation = bool(readEnvironmentVariable('NETWORK_ISOLATION', 'false'))
