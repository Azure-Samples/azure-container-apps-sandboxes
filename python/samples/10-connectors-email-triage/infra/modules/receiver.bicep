// receiver.bicep
//
// ACA environment + Container App that receives the Connector
// Gateway's trigger webhook callbacks. The receiver:
//
//   1. Validates the inbound auth (system key now; Entra-token via
//      built-in App Service auth is a future hardening — see README).
//   2. Parses the email payload from the trigger body.
//   3. Boots an ACA sandbox in the sandbox group using its
//      SystemAssigned MI.
//   4. Runs `copilot` inside the sandbox against the bundled prompt
//      that uses the Teams Managed MCP tool to post a triage card.
//   5. Returns 200 (so the trigger's at-least-once retry doesn't
//      re-fire).
//
// Storage / Log Analytics workspace are created here too so the
// container has somewhere to write logs.

@description('Location for the ACA environment + container app.')
param location string

@description('Tags applied to every resource.')
param tags object = {}

@description('Container image reference for the receiver app. Set by azd to a tag in the registry produced by the receiver service.')
param image string

@description('Resource ID of the sandbox group the receiver boots sandboxes against.')
param sandboxGroupId string

@description('Region of the sandbox group (used by the SDK endpoint resolver).')
param sandboxGroupRegion string

@description('Resource ID of the Connector Gateway (used by post-deploy to look up the API key).')
param connectorGatewayId string

@description('Name of the Teams MCP server config — receiver passes the constructed URL to the sandbox env.')
param teamsMcpServerConfigName string

@description('Resource ID of the Azure Container Registry the receiver pulls its image from.')
param containerRegistryId string

@description('ACR login server (e.g. myregistry.azurecr.io). The Container App pulls images from this server.')
param containerRegistryLoginServer string

@description('Name of the sandbox group (needed to scope the SandboxGroup Data Owner role assignment).')
param sandboxGroupName string

@description('Name suffix appended to derived resource names. Keep short.')
@minLength(2)
@maxLength(8)
param resourceToken string

var acaEnvName = 'cae-receiver-${resourceToken}'
var receiverAppName = 'ca-receiver-${resourceToken}'
var lawName = 'log-receiver-${resourceToken}'
var uaMiName = 'mi-receiver-${resourceToken}'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource ua 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: uaMiName
  location: location
  tags: tags
}

// AcrPull built-in role definition ID (constant across all of Azure).
// Grants the receiver's UA-MI permission to pull images from the registry.
var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// Reader built-in role — receiver needs ARM GET on the connector
// gateway's mcpserverConfig to discover properties.mcpEndpointUrl at
// startup.
var readerRoleDefinitionId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  // Look up by name parsed from the ARM ID we were given.
  name: last(split(containerRegistryId, '/'))
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  // Name must be a deterministic GUID so re-running the template is idempotent.
  name: guid(acr.id, ua.id, acrPullRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleDefinitionId)
    principalId: ua.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Reader on the Connector Gateway → receiver UA-MI.
resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: last(split(connectorGatewayId, '/'))
}

resource gatewayReaderRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: gateway
  name: guid(gateway.id, ua.id, readerRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleDefinitionId)
    principalId: ua.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Container Apps SandboxGroup Data Owner on the sandbox group →
// receiver UA-MI. Without this the receiver can't call
// begin_create_sandbox and per-email processing 403s.
// Role GUID confirmed via `aca sandboxgroup role create` output in
// the existing samples — it's a per-RP role definition but still
// addressable via the standard Microsoft.Authorization path.
var sandboxGroupDataOwnerRoleDefinitionId = 'c24cf47c-5077-412d-a19c-45202126392c'

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' existing = {
  name: sandboxGroupName
}

resource sandboxGroupDataOwnerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sandboxGroup
  name: guid(sandboxGroup.id, ua.id, sandboxGroupDataOwnerRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sandboxGroupDataOwnerRoleDefinitionId)
    principalId: ua.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: acaEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource receiver 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: receiverAppName
  location: location
  tags: union(tags, {
    // azd looks up the receiver Container App by this tag during
    // `azd deploy receiver`. Don't rename it.
    'azd-service-name': 'receiver'
  })
  // Wait until the AcrPull role is in place before the container app
  // tries to pull. Without this, first deployment intermittently fails
  // with "image pull error".
  dependsOn: [
    acrPullRoleAssignment
    gatewayReaderRoleAssignment
    sandboxGroupDataOwnerRoleAssignment
  ]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${ua.id}': {} }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        traffic: [
          { weight: 100, latestRevision: true }
        ]
      }
      activeRevisionsMode: 'Single'
      // Pull images from our ACR using the user-assigned MI (no admin user,
      // no username/password). The AcrPull role grant above gives the MI the
      // pull permission; the registries[] entry below tells ACA to use it.
      registries: [
        {
          server: containerRegistryLoginServer
          identity: ua.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'receiver'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: ua.properties.clientId
            }
            {
              name: 'ACA_SANDBOX_GROUP_ID'
              value: sandboxGroupId
            }
            {
              name: 'ACA_SANDBOX_GROUP_REGION'
              value: sandboxGroupRegion
            }
            {
              name: 'CONNECTOR_GATEWAY_ID'
              value: connectorGatewayId
            }
            {
              name: 'TEAMS_MCP_SERVER_CONFIG_NAME'
              value: teamsMcpServerConfigName
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 5
        rules: [
          {
            name: 'http-burst'
            http: {
              metadata: { concurrentRequests: '10' }
            }
          }
        ]
      }
    }
  }
}

@description('Public FQDN of the receiver Container App. The trigger config callbackUrl points at https://{fqdn}/webhook.')
output fqdn string = receiver.properties.configuration.ingress.fqdn

@description('Public callback URL the Connector Gateway trigger config will POST to.')
output callbackUrl string = 'https://${receiver.properties.configuration.ingress.fqdn}/webhook'

@description('Receiver Container App name (for diagnostics).')
output containerAppName string = receiver.name

@description('User-assigned MI principalId — needed to grant data-plane access on the sandbox group and to the Connector Gateway.')
output principalId string = ua.properties.principalId

@description('User-assigned MI client ID — receiver code uses this for DefaultAzureCredential.')
output clientId string = ua.properties.clientId

@description('User-assigned MI resource ID (for cross-module grants).')
output managedIdentityId string = ua.id
