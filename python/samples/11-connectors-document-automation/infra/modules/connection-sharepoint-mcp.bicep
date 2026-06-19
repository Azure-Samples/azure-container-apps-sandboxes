// connection-sharepoint-mcp.bicep
//
// Connection of type `workiqsharepoint` (Work IQ SharePoint MCP,
// preview). Same pattern as scenario 10's connection-teams.bicep:
// the namespace exposes SharePoint AI-style capabilities via a
// managed MCP backend, accessed through this connection.
//
// Once the matching mcpserverConfig (kind=ManagedMcpServer) is
// provisioned, the sandbox calls the namespace's published MCP URL
// and the namespace proxies JSON-RPC traffic to the upstream Work IQ
// SharePoint MCP server. Tool catalog (list files, get content,
// upload, etc.) is published by the upstream server via tools/list.
//
// OAuth consent is completed once out-of-band by the post-deploy
// script (drives the official `listConsentLinks` +
// `confirmConsentCode` ARM APIs with a loopback redirect listener).

@description('Parent Connector Namespace resource name.')
param gatewayName string

@description('Name for the connection (2-64 chars).')
@minLength(2)
@maxLength(64)
param name string

@description('Friendly display name shown in the Connector Namespace portal.')
param displayName string = 'SharePoint (Work IQ MCP)'

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource connection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: gateway
  name: name
  properties: {
    displayName: displayName
    connectorName: 'workiqsharepoint'
  }
}

@description('Connection runtime URL (informational — referenced by mcpserver-sharepoint.connectors[0].connectionName).')
output runtimeUrl string = connection.properties.connectionRuntimeUrl

output name string = connection.name
output id string = connection.id
