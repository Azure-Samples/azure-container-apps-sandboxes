// sandbox-group.bicep
//
// Provisions a Microsoft.App/sandboxGroups resource — the container
// that holds the ephemeral ACA microVM sandboxes the receiver boots
// per email event. Identity is SystemAssigned so the post-deploy
// script can grant the egress proxy access to the Connector Gateway's
// MCP API key for the Transform rule.
//
// Egress policy (deny-default + Transform rule that stamps X-API-Key
// on the MCP endpoint) is configured at runtime by the post-deploy
// script, not in Bicep — the API key isn't available until the
// gateway exists, and we want the key kept out of the Bicep state.

@description('Sandbox group name (3-64 chars, alphanumeric + hyphen).')
@minLength(3)
@maxLength(64)
param name string

@description('Azure region for the sandbox group. Must be a region where ACA sandboxes are available (e.g., westus2).')
param location string

@description('Tags applied to the sandbox group resource.')
param tags object = {}

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

@description('Sandbox group resource ID — pass to the receiver as env so the SDK targets it.')
output id string = sandboxGroup.id

@description('Sandbox group name.')
output name string = sandboxGroup.name

@description('Region where the sandbox group lives — needed by the SDK endpoint resolver.')
output location string = sandboxGroup.location

@description('SystemAssigned MI principalId.')
output principalId string = sandboxGroup.identity.principalId
