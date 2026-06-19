// mcpserver-sharepoint.bicep
//
// Managed MCP server config on the Connector Namespace. Forwards every
// MCP request to the upstream Work IQ SharePoint MCP server
// (`workiqsharepoint`) via the connection created in
// `connection-sharepoint-mcp.bicep`.
//
// kind=ManagedMcpServer => the namespace publishes a single MCP HTTP
// endpoint and proxies all JSON-RPC traffic to the downstream MCP
// server. We don't enumerate per-tool operations[] in Bicep — the
// downstream server publishes its tool catalog (e.g., list files,
// get file content, upload file) via the standard `tools/list`.
//
// Runtime endpoint:
//   https://{host}/api/connectorGateways/{connectorGatewayId}/mcpServerConfigs/{name}/mcp
// Auth: X-API-Key (sandbox egress proxy stamps it on the way out).

@description('Parent Connector Namespace resource name.')
param gatewayName string

@description('Name for the MCP server config (2-64 chars).')
@minLength(2)
@maxLength(64)
param name string

@description('Description shown to MCP clients via tools/list.')
param mcpDescription string = 'SharePoint (Work IQ MCP) — list files, read file content, upload files to libraries.'

@description('SharePoint MCP connection name created by connection-sharepoint-mcp.')
param sharepointMcpConnectionName string

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource mcp 'Microsoft.Web/connectorGateways/mcpserverConfigs@2026-05-01-preview' = {
  parent: gateway
  name: name
  kind: 'ManagedMcpServer'
  properties: {
    description: mcpDescription
    connectors: [
      {
        name: 'workiqsharepoint'
        connectionName: sharepointMcpConnectionName
        // ManagedMcpServer requires exactly one operation per connector;
        // 'mcp_SharePointRemoteServer' is the upstream MCP endpoint that
        // the namespace proxies JSON-RPC traffic to (per the managedMcpOperations
        // catalog response). The downstream server publishes its tool
        // catalog via tools/list.
        operations: [
          {
            name: 'mcp_SharePointRemoteServer'
            displayName: 'SharePoint MCP Server'
            description: 'Upstream MCP endpoint that proxies JSON-RPC traffic to the Work IQ SharePoint MCP server.'
          }
        ]
      }
    ]
  }
}

@description('MCP server config name (used to construct the runtime URL).')
output name string = mcp.name

@description('Resource ID of the MCP server config.')
output id string = mcp.id
