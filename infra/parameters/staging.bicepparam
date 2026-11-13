using '../main.bicep'

param environmentName = 'staging'
param location = 'eastus2'
param projectName = 'legalmind'
param apimPublisherEmail = readEnvironmentVariable('APIM_PUBLISHER_EMAIL', 'admin@example.com')

// Image tags are set at deploy time (scripts/deploy.sh, CD workflow); empty = placeholder image.
param apiImageTag = readEnvironmentVariable('API_IMAGE_TAG', '')
param frontendImageTag = readEnvironmentVariable('FRONTEND_IMAGE_TAG', '')
