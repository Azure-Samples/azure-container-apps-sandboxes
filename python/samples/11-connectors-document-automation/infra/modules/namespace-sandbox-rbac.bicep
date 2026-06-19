// namespace-sandbox-rbac.bicep
//
// Grants two principals the role needed to operate against the
// sandbox group:
//
//   - The Connector Namespace's SystemAssigned MI (required so the
//     ADC proxy can wake the host sandbox in response to a trigger)
//   - The operator/azd user (required for `azd up` -> post-deploy
//     to create, list, exec into, and configure the host sandbox).
//     Without this, the very first run fails with 403 Forbidden on
//     `list_sandboxes` / `begin_create_sandbox` during post-deploy.
//
// Role: "Container Apps SandboxGroup Data Owner"
//   GUID: c24cf47c-5077-412d-a19c-45202126392c
//
// The role grant alone isn't sufficient for the trigger path — the
// sandbox's port registration (post-deploy) ALSO has to put the
// namespace MI's objectId into its Entra allowlist
// (`auth.entraId.objectIds`). Both must be true for the proxy to
// wake the sandbox.

@description('Sandbox group resource name (scope of the role assignment).')
param sandboxGroupName string

@description('Connector Namespace MI principal ID.')
param gatewayPrincipalId string

@description('Operator / azd user principal ID. Pass empty string to skip the operator grant (e.g., in CI where the deployer is a service principal that already has access).')
param operatorPrincipalId string = ''

@description('Container Apps SandboxGroup Data Owner role definition GUID. Documented in azure-rbac as the role required for sandbox wake/create/delete operations.')
param sandboxGroupDataOwnerRoleDefinitionId string = 'c24cf47c-5077-412d-a19c-45202126392c'

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' existing = {
  name: sandboxGroupName
}

resource gatewayRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sandboxGroup
  name: guid(sandboxGroup.id, gatewayPrincipalId, sandboxGroupDataOwnerRoleDefinitionId)
  properties: {
    principalId: gatewayPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sandboxGroupDataOwnerRoleDefinitionId)
    principalType: 'ServicePrincipal'
  }
}

resource operatorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorPrincipalId)) {
  scope: sandboxGroup
  name: guid(sandboxGroup.id, operatorPrincipalId, sandboxGroupDataOwnerRoleDefinitionId)
  properties: {
    principalId: operatorPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sandboxGroupDataOwnerRoleDefinitionId)
    // Leave principalType unset — `azd up` runs as either a User or a
    // ServicePrincipal depending on context, and pinning it wrong
    // causes a PrincipalTypeNotSupported error at deploy time.
  }
}

output gatewayRoleAssignmentId string = gatewayRoleAssignment.id
output operatorRoleAssignmentId string = empty(operatorPrincipalId) ? '' : operatorRoleAssignment.id

