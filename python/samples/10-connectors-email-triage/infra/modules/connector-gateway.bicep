// connector-gateway.bicep
//
// Provisions a Microsoft.Web/connectorGateways resource with a
// SystemAssigned managed identity. The gateway hosts:
//   - connections (this scenario: Office365 + Teams)
//   - mcpserverConfigs (this scenario: Teams "Post message" tool)
//   - triggerConfigs (this scenario: "When a new email arrives" → receiver)
//
// API version: 2026-05-01-preview (Build 2026 preview).
//
// The gateway is its own integration runtime — your compute (the ACA
// receiver in this scenario) doesn't reference it directly, it just
// receives webhook callbacks from the triggerConfig and (optionally)
// makes outbound MCP calls through the egress proxy.

@description('Connector Gateway (a.k.a. connector namespace) name. 2-64 chars, alphanumeric + hyphen + underscore.')
@minLength(2)
@maxLength(64)
param name string

@description('Azure region for the gateway. Must be a Connector Gateway preview-supported region.')
param location string

@description('Tags applied to every resource the module emits.')
param tags object = {}

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

@description('Connector Gateway resource ID.')
output id string = gateway.id

@description('Connector Gateway resource name (handy for child modules).')
output name string = gateway.name

@description('System-assigned MI principalId on the gateway. Grant downstream Azure access (e.g. callbacks into ACA) to this principal.')
output principalId string = gateway.identity.principalId

@description('System-assigned MI tenantId on the gateway.')
output tenantId string = gateway.identity.tenantId
