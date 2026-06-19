// connection-office365.bicep
//
// Office 365 (Outlook) connection on a Connector Gateway. After the
// connection is provisioned, you complete OAuth consent for the mailbox
// account by opening a consent link (returned by the data-plane
// `listConsentLinks` API). The post-deploy script does that for you.
//
// This connection is consumed by:
//   - the "When a new email arrives" trigger config (this scenario)
//   - any MCP server config that exposes Office 365 actions

@description('Parent Connector Gateway resource name.')
param gatewayName string

@description('Name for the connection (2-64 chars, alphanumeric + hyphen + underscore).')
@minLength(2)
@maxLength(64)
param name string

@description('Friendly display name shown in the Connector Namespace portal.')
param displayName string = 'Office 365 (Outlook)'

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource connection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: gateway
  name: name
  properties: {
    displayName: displayName
    connectorName: 'Office365'
  }
}

@description('Connection resource name (use for trigger configs that reference this connection).')
output name string = connection.name

@description('Connection resource ID.')
output id string = connection.id
