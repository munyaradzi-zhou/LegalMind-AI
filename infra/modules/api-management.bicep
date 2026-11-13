// ---------------------------------------------------------------------------
// Module: api-management.bicep
// Azure API Management — Consumption tier with LegalMind API definition
// ---------------------------------------------------------------------------

@description('Azure region for the APIM resource.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Publisher email address for the APIM instance.')
param publisherEmail string = 'admin@legalmind.ai'

@description('Publisher organization name for the APIM instance.')
param publisherName string = 'LegalMind AI'

@description('Backend URL of the Container App (e.g., https://ca-legalmind-dev.xxxx.azurecontainerapps.io).')
param backendUrl string

@description('Application Insights resource ID for APIM diagnostics.')
param appInsightsId string = ''

@description('Application Insights instrumentation key for APIM logging.')
param appInsightsInstrumentationKey string = ''

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Short hash of the resource group: these names must be globally unique in Azure.
var suffix = take(uniqueString(resourceGroup().id), 5)
var apimName = 'apim-${projectName}-${environmentName}-${suffix}'

// Rate limit by environment (calls per minute)
var rateLimitPerMinute = environmentName == 'prod' ? 300 : (environmentName == 'staging' ? 120 : 60)

// ---------------------------------------------------------------------------
// API Management Instance (Consumption tier)
// ---------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: apimName
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

// ---------------------------------------------------------------------------
// Application Insights Logger (conditional)
// ---------------------------------------------------------------------------
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2023-09-01-preview' = if (!empty(appInsightsId)) {
  parent: apim
  name: 'appinsights-logger'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsightsId
    credentials: {
      instrumentationKey: appInsightsInstrumentationKey
    }
  }
}

// ---------------------------------------------------------------------------
// Named Value for rate limit (reusable in policies)
// ---------------------------------------------------------------------------
resource rateLimitNamedValue 'Microsoft.ApiManagement/service/namedValues@2023-09-01-preview' = {
  parent: apim
  name: 'rate-limit-per-minute'
  properties: {
    displayName: 'rate-limit-per-minute'
    value: string(rateLimitPerMinute)
    secret: false
  }
}

// ---------------------------------------------------------------------------
// Backend: Container App
// ---------------------------------------------------------------------------
resource backend 'Microsoft.ApiManagement/service/backends@2023-09-01-preview' = {
  parent: apim
  name: 'legalmind-backend'
  properties: {
    protocol: 'http'
    url: backendUrl
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

// ---------------------------------------------------------------------------
// API: LegalMind
// ---------------------------------------------------------------------------
resource api 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'legalmind-api'
  properties: {
    displayName: 'LegalMind API'
    description: 'API for the LegalMind AI legal assistant platform'
    path: 'api'
    protocols: ['https']
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'X-API-Key'
      query: 'api-key'
    }
    serviceUrl: backendUrl
    apiType: 'http'
  }
}

// ---------------------------------------------------------------------------
// API Policy: rate-limiting & CORS
// ---------------------------------------------------------------------------
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'xml'
    value: replace('''
<policies>
  <inbound>
    <base />
    <rate-limit calls="__RATE_LIMIT__" renewal-period="60" />
    <cors allow-credentials="false">
      <allowed-origins>
        <origin>*</origin>
      </allowed-origins>
      <allowed-methods>
        <method>GET</method>
        <method>POST</method>
        <method>PUT</method>
        <method>DELETE</method>
        <method>OPTIONS</method>
      </allowed-methods>
      <allowed-headers>
        <header>*</header>
      </allowed-headers>
    </cors>
    <set-backend-service backend-id="legalmind-backend" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>''', '__RATE_LIMIT__', string(rateLimitPerMinute)) // ''' strings don't interpolate ${}
  }
  dependsOn: [
    backend
  ]
}

// ---------------------------------------------------------------------------
// Operation: POST /chat
// ---------------------------------------------------------------------------
resource chatOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: api
  name: 'post-chat'
  properties: {
    displayName: 'Chat'
    description: 'Send a chat message to the LegalMind AI assistant'
    method: 'POST'
    urlTemplate: '/chat'
    request: {
      representations: [
        {
          contentType: 'application/json'
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'Successful chat response'
        representations: [
          {
            contentType: 'application/json'
          }
        ]
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Operation: POST /search
// ---------------------------------------------------------------------------
resource searchOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: api
  name: 'post-search'
  properties: {
    displayName: 'Search'
    description: 'Search legal documents using AI-powered semantic search'
    method: 'POST'
    urlTemplate: '/search'
    request: {
      representations: [
        {
          contentType: 'application/json'
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'Search results'
        representations: [
          {
            contentType: 'application/json'
          }
        ]
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Operation: POST /ingest
// ---------------------------------------------------------------------------
resource ingestOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: api
  name: 'post-ingest'
  properties: {
    displayName: 'Ingest'
    description: 'Ingest and process legal documents into the knowledge base'
    method: 'POST'
    urlTemplate: '/ingest'
    request: {
      representations: [
        {
          contentType: 'application/json'
        }
      ]
    }
    responses: [
      {
        statusCode: 202
        description: 'Document accepted for processing'
        representations: [
          {
            contentType: 'application/json'
          }
        ]
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the API Management instance.')
output apimId string = apim.id

@description('Name of the API Management instance.')
output apimName string = apim.name

@description('Gateway URL of the API Management instance.')
output apimGatewayUrl string = apim.properties.gatewayUrl

@description('Principal ID of the APIM system-assigned managed identity.')
output apimPrincipalId string = apim.identity.principalId
