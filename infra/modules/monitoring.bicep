// ---------------------------------------------------------------------------
// Module: monitoring.bicep
// Azure Monitor — Log Analytics Workspace + Application Insights
// ---------------------------------------------------------------------------

@description('Azure region for all monitoring resources.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var tags = {
  project: 'legalmind'
  environment: environmentName
}

var logAnalyticsName = 'log-${projectName}-${environmentName}'
var appInsightsName = 'appi-${projectName}-${environmentName}'

// ---------------------------------------------------------------------------
// Log Analytics Workspace
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: environmentName == 'prod' ? 90 : 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Application Insights (linked to Log Analytics)
// ---------------------------------------------------------------------------
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Log Analytics workspace.')
output logAnalyticsId string = logAnalytics.id

@description('Name of the Log Analytics workspace.')
output logAnalyticsName string = logAnalytics.name

@description('Customer ID (workspace ID) of the Log Analytics workspace.')
output logAnalyticsCustomerId string = logAnalytics.properties.customerId

@description('Resource ID of Application Insights.')
output appInsightsId string = appInsights.id

@description('Name of the Application Insights resource.')
output appInsightsName string = appInsights.name

@description('Application Insights connection string.')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

@description('Application Insights instrumentation key.')
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
