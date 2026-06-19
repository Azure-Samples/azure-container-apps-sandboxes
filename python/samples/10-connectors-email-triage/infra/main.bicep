// main.bicep — top-level azd template for scenario 10.
//
// Provisions, in order:
//
//   1. Connector Gateway (the namespace) with SystemAssigned MI
//   2. Office 365 connection (mailbox source)
//   3. Teams connection (notification sink)
//   4. Teams Managed MCP server config (publishes the "Post message"
//      operation as an MCP tool the sandbox can call)
//   5. Sandbox group (where per-email sandboxes boot)
//   6. Receiver Container App (webhook handler that boots sandboxes)
//   7. Trigger config (subscribes Outlook → POSTs to the receiver)
//
// Resources 4 and 6 must be ready before resource 7 (the trigger config
// has the receiver's public URL embedded in its callbackUrl). Bicep
// handles the ordering implicitly via `dependsOn` from module outputs.
//
// Runtime config the receiver needs (the API key for the egress proxy
// Transform rule and the OAuth consent links for the two connections)
// is intentionally NOT in Bicep — the post-deploy script fetches it
// from the data plane and writes it to the egress policy. That keeps
// credentials out of azd's deployment state.

targetScope = 'resourceGroup'

@description('Short environment name appended to derived resource names (e.g., dev, demo). 2-32 chars.')
@minLength(2)
@maxLength(32)
param environmentName string

@description('Azure region for the Connector Gateway and the receiver Container App. Must be a Connector Gateway preview region.')
param location string = resourceGroup().location

@description('Azure region for the sandbox group. Must be a region where ACA sandboxes are available (e.g., westus2). Can differ from `location`.')
param sandboxRegion string = location

@description('Container image reference for the receiver app. Default is a tiny ACA quickstart image so the template provisions cleanly before `azd deploy receiver` swaps it for the real image.')
param receiverImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Tags applied to every resource.')
param tags object = {
  'azd-env-name': environmentName
  scenario: 'connectors-email-triage'
}

// ---- Naming -----------------------------------------------------------
// One short token per environment, used as a suffix on derived names.
// uniqueString() keeps it deterministic per (sub, rg, env) tuple, so
// re-running `azd up` against the same env is idempotent.
var resourceToken = take(uniqueString(resourceGroup().id, environmentName), 6)
var connectorGatewayName = 'cg-emailtriage-${resourceToken}'
var office365ConnectionName = 'office365-${resourceToken}'
// New name: the old 'teams-*' connection was created against the
// classic 'Teams' connector which doesn't actually exist in this
// gateway's catalog. Renaming forces a fresh resource against
// 'a365teamsmcp' (Work IQ Teams), which is what's actually available.
var teamsConnectionName = 'teamsmcp-${resourceToken}'
var teamsMcpServerConfigName = 'teamsmcp-${resourceToken}'
var triggerConfigName = 'onnewemail-${resourceToken}'
var sandboxGroupName = 'sg-emailtriage-${resourceToken}'
// ACR names must be 5-50 chars, alphanumeric only (no hyphens).
var containerRegistryName = 'cremailtriage${resourceToken}'

// ---- 0. Container Registry (azd needs this to push the receiver image) ---
module registry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    name: containerRegistryName
    location: location
    tags: tags
  }
}

// ---- 1. Connector Gateway -------------------------------------------------
module gateway 'modules/connector-gateway.bicep' = {
  name: 'gateway'
  params: {
    name: connectorGatewayName
    location: location
    tags: tags
  }
}

// ---- 2. Office 365 connection ----------------------------------------
module office365Connection 'modules/connection-office365.bicep' = {
  name: 'connection-office365'
  params: {
    gatewayName: gateway.outputs.name
    name: office365ConnectionName
  }
}

// ---- 3. Teams connection ---------------------------------------------
module teamsConnection 'modules/connection-teams.bicep' = {
  name: 'connection-teams'
  params: {
    gatewayName: gateway.outputs.name
    name: teamsConnectionName
  }
}

// ---- 4. Teams Managed MCP server config ------------------------------
module teamsMcp 'modules/mcpserver-teams.bicep' = {
  name: 'mcpserver-teams'
  params: {
    gatewayName: gateway.outputs.name
    name: teamsMcpServerConfigName
    teamsConnectionName: teamsConnection.outputs.name
  }
}

// ---- 5. Sandbox group ------------------------------------------------
module sandboxGroup 'modules/sandbox-group.bicep' = {
  name: 'sandbox-group'
  params: {
    name: sandboxGroupName
    location: sandboxRegion
    tags: tags
  }
}

// ---- 6. Receiver Container App ---------------------------------------
module receiver 'modules/receiver.bicep' = {
  name: 'receiver'
  params: {
    location: location
    tags: tags
    image: receiverImage
    sandboxGroupId: sandboxGroup.outputs.id
    sandboxGroupRegion: sandboxGroup.outputs.location
    sandboxGroupName: sandboxGroup.outputs.name
    connectorGatewayId: gateway.outputs.id
    teamsMcpServerConfigName: teamsMcp.outputs.name
    containerRegistryId: registry.outputs.id
    containerRegistryLoginServer: registry.outputs.loginServer
    resourceToken: resourceToken
  }
}

// ---- 7. Trigger config (last — needs the receiver's URL) -------------
module trigger 'modules/trigger-on-new-email.bicep' = {
  name: 'trigger-on-new-email'
  params: {
    gatewayName: gateway.outputs.name
    name: triggerConfigName
    office365ConnectionName: office365Connection.outputs.name
    callbackUrl: receiver.outputs.callbackUrl
  }
}

// ---- Outputs the post-deploy script consumes -------------------------

@description('Connector Gateway resource ID. Post-deploy uses this to call listapikey and listConsentLinks.')
output connectorGatewayId string = gateway.outputs.id

@description('Connector Gateway name.')
output connectorGatewayName string = gateway.outputs.name

@description('Office 365 connection name (post-deploy generates the consent URL for this).')
output office365ConnectionName string = office365Connection.outputs.name

@description('Teams connection name (post-deploy generates the consent URL for this).')
output teamsConnectionName string = teamsConnection.outputs.name

@description('Teams MCP server config name (used by the receiver to build the MCP URL passed to each sandbox).')
output teamsMcpServerConfigName string = teamsMcp.outputs.name

@description('Sandbox group resource ID. Post-deploy uses this to install the deny-default egress policy + the X-API-Key Transform rule on the MCP host.')
output sandboxGroupId string = sandboxGroup.outputs.id

@description('Sandbox group name (for the SDK clients).')
output sandboxGroupName string = sandboxGroup.outputs.name

@description('Sandbox group region (for the SDK endpoint resolver).')
output sandboxGroupRegion string = sandboxGroup.outputs.location

@description('Receiver Container App callback URL — the trigger config posts here.')
output receiverCallbackUrl string = receiver.outputs.callbackUrl

@description('Receiver Container App name.')
output receiverContainerAppName string = receiver.outputs.containerAppName

@description('Receiver MI principalId (post-deploy grants this Container Apps SandboxGroup Data Owner on the sandbox group).')
output receiverPrincipalId string = receiver.outputs.principalId

@description('Tenant ID — needed by post-deploy when generating consent URLs.')
output tenantId string = subscription().tenantId

@description('Subscription ID — needed by post-deploy for ARM API calls.')
output subscriptionId string = subscription().subscriptionId

@description('Resource group name.')
output resourceGroupName string = resourceGroup().name

@description('Azure Container Registry login server. azd reads this as AZURE_CONTAINER_REGISTRY_ENDPOINT and uses it as the push target for `azd deploy receiver`.')
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
