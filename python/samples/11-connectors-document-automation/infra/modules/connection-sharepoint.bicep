// connection-sharepoint.bicep
//
// SharePoint Online connection used by the trigger config
// (`operationName: 'GetOnNewFileItems'` on `sharepointonline`).
//
// SharePoint shows up in this namespace TWICE under two different
// connector slugs that need separate connections + separate OAuth
// consents (per scenario 10's similar Office365 / a365teamsmcp split):
//
//   - `sharepointonline`   — classic action/trigger connector
//                            (this module). Used for the file-created
//                            trigger.
//   - `workiqsharepoint`   — Work IQ SharePoint MCP server (see
//                            `connection-sharepoint-mcp.bicep`). Used
//                            from inside the sandbox to read file
//                            content and upload extracted results.
//
// OAuth consent is completed once at deploy time by the post-deploy
// script (drives the official `listConsentLinks` +
// `confirmConsentCode` ARM APIs with a loopback redirect listener).
// Bicep just
// declares the connection; the OAuth dance happens out-of-band.

@description('Parent Connector Namespace resource name.')
param gatewayName string

@description('Name for the connection (2-64 chars, alphanumeric + hyphen + underscore).')
@minLength(2)
@maxLength(64)
param name string

@description('Friendly display name shown in the Connector Namespace portal.')
param displayName string = 'SharePoint Online'

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource connection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: gateway
  name: name
  properties: {
    displayName: displayName
    // `sharepointonline` (lowercase) is the swagger short name used by
    // the trigger config's `connectionDetails.connectorName`. Confirmed
    // from a real HAR capture of the portal creating the same trigger.
    connectorName: 'sharepointonline'
  }
}

@description('Connection resource name (referenced by trigger config + mcpserver-sharepoint).')
output name string = connection.name

@description('Connection resource ID.')
output id string = connection.id
