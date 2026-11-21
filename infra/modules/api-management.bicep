// ---------------------------------------------------------------------------
// Module: api-management.bicep
// Azure API Management in front of the API and, with aiGateway, as the AI gateway
// in front of Azure OpenAI:
//   - the app's model calls are checked (Entra token of the app identity), limited
//     per user (tokens per minute) and counted per user in Application Insights
//   - a backend pool fails over from the primary model account to a second region
//     on 429 or 5xx (circuit breaker per backend, retry in the backend section)
//   - optional semantic cache (Azure Managed Redis), partitioned by access level
// Consumption can't do token limits or circuit breakers: the AI gateway needs a
// dedicated tier (Developer for dev, Basic v2 and up for production).
// ---------------------------------------------------------------------------

@description('Azure region for the APIM resource.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Name of the APIM instance (main.bicep derives it, because the API needs the gateway URL before APIM exists).')
param name string

@description('Pricing tier. Consumption has no token limits or circuit breakers, so aiGateway needs another tier.')
@allowed(['Consumption', 'Developer', 'BasicV2', 'StandardV2'])
param sku string = 'Developer'

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

@description('Route the app\'s model calls through this gateway (needs a dedicated tier and the endpoints below).')
param aiGateway bool = false

@description('Endpoint of the primary Azure OpenAI account (e.g. https://oai-x.openai.azure.com/).')
param openAiPrimaryEndpoint string = ''

@description('Endpoint of the failover Azure OpenAI account in another region. Empty = no failover backend.')
param openAiSecondaryEndpoint string = ''

@description('Client ID of the app\'s managed identity: the only caller the AI gateway accepts.')
param appClientId string = ''

@description('Tokens per minute per user (prompt + completion), counted over all of a user\'s model calls.')
param userTokensPerMinute int = 60000

@description('Name of the Azure Managed Redis cluster for the semantic cache. Empty = no semantic cache.')
param semanticCacheRedisName string = ''

@description('How close a new prompt must be to a cached one (0-1, lower is stricter).')
param semanticCacheScoreThreshold string = '0.05'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Rate limit by environment (calls per minute)
var rateLimitPerMinute = environmentName == 'prod' ? 300 : (environmentName == 'staging' ? 120 : 60)

var hasFailover = aiGateway && !empty(openAiSecondaryEndpoint)
var semanticCache = aiGateway && !empty(semanticCacheRedisName)
var embeddingDeployment = 'text-embedding-3-large'

// ---------------------------------------------------------------------------
// API Management instance
// ---------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
    capacity: sku == 'Consumption' ? 0 : 1
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
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = if (!empty(appInsightsId)) {
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
resource rateLimitNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
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
resource backend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
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
resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
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
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
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
resource chatOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
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
resource searchOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
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
resource ingestOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
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

// ===========================================================================
// AI gateway: Azure OpenAI behind APIM
// ===========================================================================

// One rule per backend (the only number APIM allows): a single 429 or 5xx takes the
// backend out of the pool for a minute, or for as long as its Retry-After says.
var circuitBreaker = {
  rules: [
    {
      name: 'throttled-or-failing'
      failureCondition: {
        count: 1
        interval: 'PT1M'
        statusCodeRanges: [
          { min: 429, max: 429 }
          { min: 500, max: 599 }
        ]
      }
      tripDuration: 'PT1M'
      acceptRetryAfter: true
    }
  ]
}

resource openAiPrimary 'Microsoft.ApiManagement/service/backends@2024-05-01' = if (aiGateway) {
  parent: apim
  name: 'openai-primary'
  properties: {
    description: 'Primary Azure OpenAI account'
    protocol: 'http'
    url: '${openAiPrimaryEndpoint}openai'
    circuitBreaker: circuitBreaker
  }
}

resource openAiSecondary 'Microsoft.ApiManagement/service/backends@2024-05-01' = if (hasFailover) {
  parent: apim
  name: 'openai-secondary'
  properties: {
    description: 'Failover Azure OpenAI account in another region'
    protocol: 'http'
    url: '${openAiSecondaryEndpoint}openai'
    circuitBreaker: circuitBreaker
  }
}

// Priority 2 only takes traffic while every priority 1 backend is tripped
resource openAiPool 'Microsoft.ApiManagement/service/backends@2024-05-01' = if (aiGateway) {
  parent: apim
  name: 'openai-pool'
  properties: {
    description: 'Primary first, the second region while the primary is throttled or failing'
    type: 'Pool'
    pool: {
      services: concat(
        [{ id: openAiPrimary.id, priority: 1, weight: 1 }],
        hasFailover ? [{ id: openAiSecondary.id, priority: 2, weight: 1 }] : []
      )
    }
  }
}

// Embeddings for the semantic cache's similarity lookup (the full embeddings URL)
resource embeddingsBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = if (semanticCache) {
  parent: apim
  name: 'openai-embeddings'
  properties: {
    description: 'Embeddings for the semantic cache'
    protocol: 'http'
    url: '${openAiPrimaryEndpoint}openai/deployments/${embeddingDeployment}/embeddings'
  }
}

resource openAiApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (aiGateway) {
  parent: apim
  name: 'azure-openai'
  properties: {
    displayName: 'Azure OpenAI (AI gateway)'
    description: 'The app\'s model calls: token limits per user, token metrics, failover between regions.'
    path: 'openai'
    protocols: ['https']
    subscriptionRequired: false // callers prove who they are with an Entra token instead
    apiType: 'http'
  }
}

resource openAiChat 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = if (aiGateway) {
  parent: openAiApi
  name: 'chat-completions'
  properties: {
    displayName: 'Chat completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
    templateParameters: [{ name: 'deployment-id', type: 'string', required: true }]
  }
}

resource openAiEmbeddings 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = if (aiGateway) {
  parent: openAiApi
  name: 'embeddings'
  properties: {
    displayName: 'Embeddings'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/embeddings'
    templateParameters: [{ name: 'deployment-id', type: 'string', required: true }]
  }
}

// The app sends who the model call is for (x-legalmind-user, from the signed-in user)
// and a hash of what that user may see (x-legalmind-access). Only the app identity
// can call, so the headers are trusted.
var gatewayPolicy = '''
<policies>
  <inbound>
    <base />
    <validate-azure-ad-token tenant-id="__TENANT__" header-name="Authorization" failed-validation-httpcode="401" failed-validation-error-message="Only the LegalMind app identity may call the model gateway.">
      <client-application-ids>
        <application-id>__APP_CLIENT_ID__</application-id>
      </client-application-ids>
      <audiences>
        <audience>https://cognitiveservices.azure.com</audience>
      </audiences>
    </validate-azure-ad-token>
    <set-variable name="caller" value="@(context.Request.Headers.GetValueOrDefault(&quot;x-legalmind-user&quot;, &quot;service&quot;))" />
    <llm-token-limit counter-key="@((string)context.Variables[&quot;caller&quot;])" tokens-per-minute="__USER_TPM__" estimate-prompt-tokens="false" remaining-tokens-header-name="x-legalmind-user-tokens-remaining" />
    <llm-emit-token-metric namespace="legalmind-gateway">
      <dimension name="User" value="@((string)context.Variables[&quot;caller&quot;])" />
      <dimension name="Deployment" value="@(context.Request.MatchedParameters.GetValueOrDefault(&quot;deployment-id&quot;, &quot;unknown&quot;))" />
      <dimension name="Operation ID" />
    </llm-emit-token-metric>
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />
    <set-backend-service backend-id="openai-pool" />
  </inbound>
  <backend>
    <!-- A 429 or 5xx trips that backend's circuit breaker; the retry then goes to the next one in the pool -->
    <retry condition="@(context.Response != null &amp;&amp; (context.Response.StatusCode == 429 || context.Response.StatusCode &gt;= 500))" count="2" interval="1" first-fast-retry="true">
      <forward-request buffer-request-body="true" />
    </retry>
  </backend>
  <outbound>
    <base />
    <set-header name="x-legalmind-model-backend" exists-action="override">
      <value>@(context.Request.Url.Host)</value>
    </set-header>
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>'''

resource openAiApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = if (aiGateway) {
  parent: openAiApi
  name: 'policy'
  properties: {
    format: 'xml'
    value: replace(replace(replace(gatewayPolicy, '__TENANT__', tenant().tenantId), '__APP_CLIENT_ID__', appClientId), '__USER_TPM__', string(userTokensPerMinute))
  }
  dependsOn: [
    openAiPool
  ]
}

// ---------------------------------------------------------------------------
// Semantic cache (optional): chat completions only, partitioned by what the caller
// may see, so a cached answer never reaches someone without access to its sources
// ---------------------------------------------------------------------------
resource redis 'Microsoft.Cache/redisEnterprise@2025-04-01' existing = if (semanticCache) {
  name: semanticCacheRedisName
}

resource redisDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-04-01' existing = if (semanticCache) {
  parent: redis
  name: 'default'
}

resource cache 'Microsoft.ApiManagement/service/caches@2024-05-01' = if (semanticCache) {
  parent: apim
  name: 'default'
  properties: {
    description: 'Azure Managed Redis (RediSearch) for the semantic cache'
    connectionString: '${redis!.properties.hostName}:10000,password=${redisDatabase!.listKeys().primaryKey},ssl=True,abortConnect=False'
    useFromLocation: 'default'
    resourceId: '${environment().resourceManager}${substring(redis!.id, 1)}'
  }
}

var cachePolicy = '''
<policies>
  <inbound>
    <base />
    <llm-semantic-cache-lookup score-threshold="__THRESHOLD__" embeddings-backend-id="openai-embeddings" embeddings-backend-auth="system-assigned" ignore-system-messages="false" max-message-count="20">
      <vary-by>@(context.Request.Headers.GetValueOrDefault("x-legalmind-access", "none"))</vary-by>
      <vary-by>@(context.Request.MatchedParameters.GetValueOrDefault("deployment-id", ""))</vary-by>
    </llm-semantic-cache-lookup>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <llm-semantic-cache-store duration="3600" />
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>'''

resource chatCachePolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = if (semanticCache) {
  parent: openAiChat
  name: 'policy'
  properties: {
    format: 'xml'
    value: replace(cachePolicy, '__THRESHOLD__', semanticCacheScoreThreshold)
  }
  dependsOn: [
    cache
    embeddingsBackend
    openAiApiPolicy
  ]
}

// Requests, backend calls and the token metric in Application Insights
resource openAiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = if (aiGateway && !empty(appInsightsId)) {
  parent: openAiApi
  name: 'applicationinsights'
  properties: {
    loggerId: apimLogger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    metrics: true // needed by llm-emit-token-metric
    sampling: { samplingType: 'fixed', percentage: 100 }
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
