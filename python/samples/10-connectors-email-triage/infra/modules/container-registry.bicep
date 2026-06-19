// container-registry.bicep
//
// Minimal Azure Container Registry for `azd deploy receiver` to push
// the receiver image to. Basic SKU is plenty for a sample (per-image
// pull bandwidth is low, only the receiver Container App pulls).
//
// AcrPull on the receiver's user-assigned MI is granted in receiver.bicep
// so the Container App can pull without admin user / username+password.

@description('Registry name. 5-50 chars, alphanumeric only (no hyphens). Globally unique.')
@minLength(5)
@maxLength(50)
param name string

@description('Azure region.')
param location string

@description('Tags applied to the registry.')
param tags object = {}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

@description('Login server (e.g. myregistry.azurecr.io). azd exports this as AZURE_CONTAINER_REGISTRY_ENDPOINT for `azd deploy`.')
output loginServer string = registry.properties.loginServer

@description('Resource ID — for role assignments.')
output id string = registry.id

@description('Registry name.')
output name string = registry.name
