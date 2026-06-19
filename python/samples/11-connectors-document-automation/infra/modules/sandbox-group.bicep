// sandbox-group.bicep
//
// Microsoft.App/sandboxGroups — the parent resource that holds the
// long-lived "host" sandbox the Connector Namespace trigger posts to.
//
// In scenario 11, the sandbox itself is the webhook target — there
// is no receiver Container App. The Connector Namespace's MI gets
// "Container Apps SandboxGroup Data Owner" on this group so the ADC
// proxy can wake the host sandbox on every trigger (sandbox is
// registered with `activationMode: OnDemand` on its exposed port).
//
// The actual sandbox + the port registration happen in the
// post-deploy script — Bicep can't create sandboxes (they're a
// data-plane operation).

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

@description('Sandbox group resource ID — referenced by the role assignment + the post-deploy host-sandbox bootstrap.')
output id string = sandboxGroup.id

@description('Sandbox group name.')
output name string = sandboxGroup.name

@description('Region where the sandbox group lives — needed by the SDK endpoint resolver and the ADC proxy URL.')
output location string = sandboxGroup.location

@description('Sandbox group SystemAssigned MI principalId.')
output principalId string = sandboxGroup.identity.principalId
