// ---------------------------------------------------------------------------
// Module: network.bicep
// The VNet for network isolation (main.bicep networkIsolation=true): one subnet for
// the Container Apps environment, one for the data services' private endpoints.
// ---------------------------------------------------------------------------

@description('Azure region for the VNet.')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('Short project name used in resource naming.')
param projectName string

@description('Address space of the VNet.')
param addressPrefix string = '10.60.0.0/16'

var tags = {
  project: 'legalmind'
  environment: environmentName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${projectName}-${environmentName}-${location}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [addressPrefix] }
    subnets: [
      {
        // Container Apps (workload profiles): delegated, /27 at least; /23 leaves room to scale
        name: 'snet-apps'
        properties: {
          addressPrefix: cidrSubnet(addressPrefix, 23, 0)
          delegations: [{ name: 'apps', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: cidrSubnet(addressPrefix, 24, 2)
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output appsSubnetId string = vnet.properties.subnets[0].id
output privateEndpointSubnetId string = vnet.properties.subnets[1].id
