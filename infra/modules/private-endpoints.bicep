// ---------------------------------------------------------------------------
// Module: private-endpoints.bicep
// A private endpoint per data service, and the private DNS zones that make the
// services' normal host names resolve to those private IPs inside the VNet. With
// public network access disabled, the services only answer through these.
// ---------------------------------------------------------------------------

@description('Azure region for the private endpoints (the VNet\'s region).')
param location string

@description('Deployment environment name (dev, staging, prod).')
param environmentName string

@description('VNet the DNS zones are linked to.')
param vnetId string

@description('Subnet for the private endpoints.')
param subnetId string

@description('One entry per service: { name, resourceId, groupId, zone } (zone = its privatelink DNS zone).')
param endpoints array

var tags = {
  project: 'legalmind'
  environment: environmentName
}

// Several services share a zone (all Cognitive Services accounts use one).
var zones = union(map(endpoints, e => e.zone), [])

resource dnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for zone in zones: {
    name: zone
    location: 'global'
    tags: tags
  }
]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, i) in zones: {
    parent: dnsZones[i]
    name: 'link-${environmentName}'
    location: 'global'
    properties: {
      virtualNetwork: { id: vnetId }
      registrationEnabled: false
    }
  }
]

resource privateEndpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [
  for e in endpoints: {
    name: 'pe-${e.name}'
    location: location
    tags: tags
    properties: {
      subnet: { id: subnetId }
      privateLinkServiceConnections: [
        {
          name: e.name
          properties: {
            privateLinkServiceId: e.resourceId
            groupIds: [e.groupId]
          }
        }
      ]
    }
  }
]

resource zoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [
  for (e, i) in endpoints: {
    parent: privateEndpoints[i]
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [
        {
          name: replace(e.zone, '.', '-')
          properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', e.zone) }
        }
      ]
    }
    dependsOn: [dnsZones]
  }
]
