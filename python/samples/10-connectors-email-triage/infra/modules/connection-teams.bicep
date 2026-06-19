// connection-teamsmcp.bicep
//
// Connection of type `a365teamsmcp` (Work IQ Teams MCP, preview).
// This Connector Gateway preview catalog exposes Teams capabilities
// only as a hosted/managed MCP server — there is no classic
// action-based "Teams" connector in this gateway. The connection
// authenticates the Work IQ Teams MCP backend to a specific M365
// account; the upstream MCP server's tool catalog (post a message,
// search messages, etc.) becomes available on our gateway-published
// MCP endpoint once an mcpserverConfig of kind ManagedMcpServer
// references this connection.
//
// OAuth consent is completed once out-of-band — the post-deploy
// script runs `az connector-namespace connection authorize`.

@description('Parent Connector Gateway resource name.')
param gatewayName string

@description('Name for the connection (2-64 chars).')
@minLength(2)
@maxLength(64)
param name string

@description('Friendly display name shown in the Connector Namespace portal.')
param displayName string = 'Microsoft Teams (Work IQ MCP)'

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource connection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: gateway
  name: name
  properties: {
    displayName: displayName
    // a365teamsmcp is the Work IQ Teams MCP managed-API the gateway
    // catalog exposes for Teams operations. The classic "Teams"
    // connector is not currently available in this gateway preview.
    connectorName: 'a365teamsmcp'
  }
}

@description('Connection runtime URL (informational — referenced by mcpserverConfig.connectors[0].connectionName).')
output runtimeUrl string = connection.properties.connectionRuntimeUrl

output name string = connection.name
output id string = connection.id

