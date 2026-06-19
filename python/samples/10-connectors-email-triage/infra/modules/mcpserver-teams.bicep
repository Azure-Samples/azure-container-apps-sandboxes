// mcpserver-teams.bicep
//
// Managed MCP server config on the Connector Gateway. Forwards every
// MCP request to the upstream Work IQ Teams MCP server (`a365teamsmcp`)
// via the connection created in `connection-teams.bicep`.
//
// kind=ManagedMcpServer means "this gateway MCP endpoint proxies to
// a downstream managed MCP server". The downstream server publishes
// its own tools/list (e.g., post a Teams message, search messages,
// list chats) — we don't have to specify them; clients discover them
// via the standard MCP handshake.
//
// The Connector Gateway publishes the runtime endpoint at:
//   https://{host}/api/connectorGateways/{connectorGatewayId}/mcpserverconfigs/{name}/mcp
// Authentication is the gateway API key (`X-API-Key` header); our
// sandbox's egress proxy stamps it on the way out so the sandbox
// itself never holds the key.

@description('Parent Connector Gateway resource name.')
param gatewayName string

@description('Name for the MCP server config (2-64 chars).')
@minLength(2)
@maxLength(64)
param name string

@description('Description shown to MCP clients via tools/list.')
param mcpDescription string = 'Microsoft Teams (Work IQ MCP) — post messages, search chats, list channels.'

@description('Teams MCP connection name created by the connection-teams module.')
param teamsConnectionName string

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
        // For kind=ManagedMcpServer, the connectors[] array references
        // the connection backing the downstream MCP server. The runtime
        // forwards JSON-RPC requests verbatim — we don't enumerate
        // operations[] here because the downstream server publishes
        // its own tool catalog via tools/list.
        name: 'a365teamsmcp'
        connectionName: teamsConnectionName
        // ManagedMcpServer requires exactly one operation per connector;
        // 'mcp_TeamsServer' is the upstream MCP endpoint operation that
        // the gateway proxies JSON-RPC traffic to. The downstream server
        // publishes its own tool catalog via tools/list.
        operations: [
          {
            name: 'mcp_TeamsServer'
            displayName: 'Microsoft Teams MCP Server'
            description: 'Upstream MCP endpoint that proxies JSON-RPC traffic to the Work IQ Teams MCP server.'
          }
        ]
      }
    ]
  }
}

@description('MCP server config name (used by access policies and by clients to construct the runtime URL).')
output name string = mcp.name

@description('Resource ID of the MCP server config.')
output id string = mcp.id
