// ---------------------------------------------------------------------------
// Module: alerts.bicep
// Knowing something is wrong before users do: an email action group, five alert
// rules and the operations workbook. What to do when one fires: docs/runbook.md.
// The answer-quality signals come from src/legalmind/telemetry.py.
// ---------------------------------------------------------------------------

@description('Region of Application Insights (log alerts are created next to it).')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Who gets the alert emails.')
param alertEmail string

@description('Resource ID of Application Insights.')
param appInsightsId string

@description('Name of the Azure OpenAI account.')
param openAiName string

@description('Name of the API Container App.')
param apiAppName string

var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Severity: 1 = error (users affected now), 2 = warning (degraded), 3 = informational
var rules = [
  {
    name: 'api-5xx-rate'
    description: 'More than 2% of API requests failed with a 5xx in the last 5 minutes (at least 5 requests). Runbook: API 5xx rate.'
    severity: 1
    window: 'PT5M'
    frequency: 'PT5M'
    query: 'requests | where name has "/api/" | summarize total = count(), failed = countif(toint(resultCode) >= 500) | where total >= 5 and todouble(failed) / total > 0.02'
  }
  {
    name: 'chat-latency-p95'
    description: 'p95 latency of /api/chat above 20 s over 15 minutes (at least 3 requests). Runbook: Chat latency.'
    severity: 2
    window: 'PT15M'
    frequency: 'PT5M'
    query: 'requests | where name has "/api/chat" | summarize n = count(), p95 = percentile(duration, 95) | where n >= 3 and p95 > 20000'
  }
  {
    name: 'answers-unverified'
    description: 'More than 20% of answers in the last hour could not be checked against the documents (grounding = unverified; at least 5 answers). Runbook: Unverified answers.'
    severity: 2
    window: 'PT1H'
    frequency: 'PT15M'
    query: 'requests | where name has "/api/chat" | extend grounding = tostring(customDimensions["legalmind.grounding"]) | where isnotempty(grounding) | summarize total = count(), unverified = countif(grounding == "unverified") | where total >= 5 and todouble(unverified) / total > 0.2'
  }
]

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiName
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: apiAppName
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${projectName}-${environmentName}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take('lm-${environmentName}', 12)
    enabled: true
    emailReceivers: [
      { name: 'on-call', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

resource logAlerts 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = [
  for rule in rules: {
    name: 'alert-${projectName}-${environmentName}-${rule.name}'
    location: location
    tags: tags
    properties: {
      displayName: '${projectName} ${environmentName}: ${rule.name}'
      description: rule.description
      severity: rule.severity
      enabled: true
      scopes: [appInsightsId]
      evaluationFrequency: rule.frequency
      windowSize: rule.window
      criteria: {
        allOf: [
          {
            query: rule.query
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
          }
        ]
      }
      autoMitigate: true
      actions: { actionGroups: [actionGroup.id] }
    }
  }
]

// The model is throttling us: callers see slow answers or errors (openai.RateLimitError)
resource openAiThrottled 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${projectName}-${environmentName}-openai-429'
  location: 'global'
  tags: tags
  properties: {
    description: 'Azure OpenAI answered 429 (rate limited) in the last 5 minutes. Runbook: OpenAI throttling.'
    severity: 2
    enabled: true
    scopes: [openAi.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'throttled'
          metricName: 'AzureOpenAIRequests'
          metricNamespace: 'Microsoft.CognitiveServices/accounts'
          dimensions: [{ name: 'StatusCode', operator: 'Include', values: ['429'] }]
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

resource apiRestarts 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${projectName}-${environmentName}-api-restarts'
  location: 'global'
  tags: tags
  properties: {
    description: 'The API container restarted in the last 5 minutes (crash, failed probe or out of memory). Runbook: Container restarts.'
    severity: 1
    enabled: true
    scopes: [apiApp.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'restarts'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

// ---------------------------------------------------------------------------
// Operations workbook
// ---------------------------------------------------------------------------
// gpt-4.1-mini list price, converted to EUR (USD 0.40 / 1.60 per million tokens)
var eurPerMillionInput = '0.37'
var eurPerMillionOutput = '1.47'

var panels = [
  {
    title: 'Traffic: API requests and failures per hour'
    visualization: 'timechart'
    query: 'requests | where name has "/api/" | summarize requests = count(), failed = countif(success == false) by bin(timestamp, 1h)'
  }
  {
    title: 'Latency by stage (ms)'
    visualization: 'table'
    query: 'dependencies | where name startswith "legalmind." | summarize calls = count(), p50 = round(percentile(duration, 50)), p95 = round(percentile(duration, 95)) by stage = name | order by p95 desc'
  }
  {
    title: 'Grounding labels of answers over time'
    visualization: 'areachart'
    query: 'requests | where name has "/api/chat" | extend grounding = tostring(customDimensions["legalmind.grounding"]) | where isnotempty(grounding) | summarize answers = count() by grounding, bin(timestamp, 1h)'
  }
  {
    title: 'Tokens and model cost per day (EUR, gpt-4.1-mini list price)'
    visualization: 'table'
    query: 'dependencies | extend input = toint(customDimensions["gen_ai.usage.input_tokens"]), output = toint(customDimensions["gen_ai.usage.output_tokens"]) | where isnotnull(input) | summarize calls = count(), input_tokens = sum(input), output_tokens = sum(output) by day = bin(timestamp, 1d) | extend cost_eur = round(input_tokens * ${eurPerMillionInput} / 1e6 + output_tokens * ${eurPerMillionOutput} / 1e6, 2) | order by day desc'
  }
  {
    title: 'Agents used'
    visualization: 'piechart'
    query: 'requests | where name has "/api/chat" | extend agent = tostring(customDimensions["legalmind.agent"]) | where isnotempty(agent) | summarize answers = count() by agent'
  }
  {
    title: 'Top failing dependencies'
    visualization: 'table'
    query: 'dependencies | where success == false | summarize failures = count(), last = max(timestamp) by target, type, resultCode | top 10 by failures'
  }
]

var workbookItems = [
  for (panel, i) in panels: {
    type: 3
    name: 'panel-${i}'
    content: {
      version: 'KqlItem/1.0'
      title: panel.title
      query: panel.query
      queryType: 0
      resourceType: 'microsoft.insights/components'
      visualization: panel.visualization
      timeContext: { durationMs: 604800000 } // last 7 days
      size: 0
    }
  }
]

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'legalmind-operations-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'LegalMind operations (${environmentName})'
    category: 'workbook'
    sourceId: appInsightsId
    serializedData: string({
      version: 'Notebook/1.0'
      items: workbookItems
      fallbackResourceIds: [appInsightsId]
    })
  }
}

output actionGroupId string = actionGroup.id
output workbookId string = workbook.id
