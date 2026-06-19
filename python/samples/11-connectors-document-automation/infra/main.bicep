// main.bicep — top-level azd template for scenario 11.
//
// Provisions (in order):
//
//   1. Connector Namespace (SystemAssigned MI)
//   2. SharePoint Online connection (classic — for the trigger)
//   3. SharePoint MCP connection (workiqsharepoint — for the
//      sandbox to read/write files inside SharePoint)
//   4. SharePoint Managed MCP server config (publishes the Work IQ
//      SharePoint MCP server's tool catalog on a namespace-published
//      MCP HTTP endpoint, accessed by the sandbox)
//   5. Sandbox group (SystemAssigned MI)
//   6. RBAC: Connector Namespace MI -> "Container Apps SandboxGroup
//      Data Owner" on the sandbox group
//
// Everything else (the host sandbox itself, the port registration on
// the ADC proxy, the trigger config — which needs the resolved
// sandbox ID + URL — and the OAuth consent flows) is done by the
// post-deploy script in `infra/scripts/postdeploy.{sh,ps1}`.
//
// Why? Sandboxes are a data-plane operation (you can't create them
// from Bicep). And the trigger config's `callbackUrl` /
// `metadata.sandboxId` aren't known until after the sandbox boots.

targetScope = 'resourceGroup'

@description('Short environment name appended to derived resource names. 2-32 chars.')
@minLength(2)
@maxLength(32)
param environmentName string

@description('Azure region for the Connector Namespace. Must be a Connector Namespaces preview region (currently only westcentralus).')
param location string = resourceGroup().location

@description('Azure region for the sandbox group. Must be a region where ACA sandboxes are available (e.g., westus2). Can differ from `location`.')
param sandboxRegion string = location

@description('Principal ID of the operator running azd up. Granted Container Apps SandboxGroup Data Owner on the sandbox group so post-deploy can create + manage the host sandbox. Wired by main.parameters.json to azd built-in AZURE_PRINCIPAL_ID.')
param operatorPrincipalId string = ''

@description('Tags applied to every resource.')
param tags object = {
  'azd-env-name': environmentName
  scenario: 'connectors-document-automation'
}

// ---- Naming -----------------------------------------------------------
var resourceToken = take(uniqueString(resourceGroup().id, environmentName), 6)
var connectorGatewayName = 'cg-docauto-${resourceToken}'
var sharepointConnectionName = 'sharepoint-${resourceToken}'
var sharepointMcpConnectionName = 'spmcp-${resourceToken}'
var sharepointMcpServerConfigName = 'spmcp-${resourceToken}'
var sandboxGroupName = 'sg-docauto-${resourceToken}'

// ---- 1. Connector Namespace -------------------------------------------------
module gateway 'modules/connector-namespace.bicep' = {
  name: 'gateway'
  params: {
    name: connectorGatewayName
    location: location
    tags: tags
  }
}

// ---- 2. SharePoint Online connection (for the trigger) ------------------
module sharepointConnection 'modules/connection-sharepoint.bicep' = {
  name: 'connection-sharepoint'
  params: {
    gatewayName: gateway.outputs.name
    name: sharepointConnectionName
  }
}

// ---- 3. SharePoint MCP connection (for the sandbox -> SharePoint) -------
module sharepointMcpConnection 'modules/connection-sharepoint-mcp.bicep' = {
  name: 'connection-sharepoint-mcp'
  params: {
    gatewayName: gateway.outputs.name
    name: sharepointMcpConnectionName
  }
}

// ---- 4. SharePoint Managed MCP server config ---------------------------
module sharepointMcp 'modules/mcpserver-sharepoint.bicep' = {
  name: 'mcpserver-sharepoint'
  params: {
    gatewayName: gateway.outputs.name
    name: sharepointMcpServerConfigName
    sharepointMcpConnectionName: sharepointMcpConnection.outputs.name
  }
}

// ---- 5. Sandbox group --------------------------------------------------
module sandboxGroup 'modules/sandbox-group.bicep' = {
  name: 'sandbox-group'
  params: {
    name: sandboxGroupName
    location: sandboxRegion
    tags: tags
  }
}

// ---- 6. RBAC: Namespace MI + operator -> Sandbox Group Data Owner ----
module gatewaySandboxRbac 'modules/namespace-sandbox-rbac.bicep' = {
  name: 'gateway-sandbox-rbac'
  params: {
    sandboxGroupName: sandboxGroup.outputs.name
    gatewayPrincipalId: gateway.outputs.principalId
    operatorPrincipalId: operatorPrincipalId
  }
}

// ---- Outputs the post-deploy script consumes ---------------------------

@description('Connector Namespace resource ID. Post-deploy uses this to call listApiKey, create the trigger config, and read the namespace MI principalId.')
output connectorGatewayId string = gateway.outputs.id

@description('Connector Namespace name.')
output connectorGatewayName string = gateway.outputs.name

@description('Connector Namespace SystemAssigned MI principalId. Goes into the sandbox port allowlist (Entra objectIds).')
output gatewayPrincipalId string = gateway.outputs.principalId

@description('SharePoint Online connection name (post-deploy generates the consent URL).')
output sharepointConnectionName string = sharepointConnection.outputs.name

@description('SharePoint MCP (workiqsharepoint) connection name (post-deploy generates the consent URL).')
output sharepointMcpConnectionName string = sharepointMcpConnection.outputs.name

@description('SharePoint MCP server config name. Sandbox listener reads this to construct the runtime MCP URL it points Copilot CLI at.')
output sharepointMcpServerConfigName string = sharepointMcp.outputs.name

@description('Sandbox group resource ID — post-deploy uses this to create the host sandbox + register port 8080.')
output sandboxGroupId string = sandboxGroup.outputs.id

@description('Sandbox group name (for the SDK clients and the trigger config metadata).')
output sandboxGroupName string = sandboxGroup.outputs.name

@description('Sandbox group region (for the SDK endpoint resolver and the adcproxy.io URL host).')
output sandboxGroupRegion string = sandboxGroup.outputs.location

@description('Tenant ID — needed by post-deploy when writing the port allowlist and when generating consent URLs.')
output tenantId string = subscription().tenantId

@description('Subscription ID — needed by post-deploy for ARM API calls.')
output subscriptionId string = subscription().subscriptionId

@description('Resource group name.')
output resourceGroupName string = resourceGroup().name
