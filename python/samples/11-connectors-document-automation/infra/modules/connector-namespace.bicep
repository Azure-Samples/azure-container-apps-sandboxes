// connector-namespace.bicep
//
// Provisions a Microsoft.Web/connectorGateways resource with a
// SystemAssigned managed identity. The namespace hosts:
//   - connections (this scenario: SharePoint Online)
//   - mcpserverConfigs (this scenario: Work IQ SharePoint MCP)
//   - triggerConfigs (this scenario: "When a file is created (properties only)")
//
// Unlike scenario 10, the trigger's callback URL points at the
// sandbox directly through the ADC proxy — the namespace MI is what
// signs the outbound POST to the sandbox. We grant that MI
// "Container Apps SandboxGroup Data Owner" on the sandbox group
// so the ADC proxy will wake the sandbox on demand.

@description('Connector Namespace (a.k.a. connector namespace) name. 2-64 chars, alphanumeric + hyphen + underscore.')
@minLength(2)
@maxLength(64)
param name string

@description('Azure region for the Connector Namespace. Must be a Connector Namespace preview-supported region (currently only westcentralus).')
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

@description('Connector Namespace resource ID.')
output id string = gateway.id

@description('Connector Namespace resource name (handy for child modules).')
output name string = gateway.name

@description('System-assigned MI principalId on the namespace. Goes into the sandbox port allowlist (Entra objectIds) AND gets SandboxGroup Data Owner on the sandbox group.')
output principalId string = gateway.identity.principalId

@description('System-assigned MI tenantId on the namespace.')
output tenantId string = gateway.identity.tenantId
