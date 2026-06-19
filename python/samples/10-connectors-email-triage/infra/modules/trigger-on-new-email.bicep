// trigger-on-new-email.bicep
//
// Registers a triggerConfig on the Connector Gateway that subscribes
// to Office 365 Outlook's "When a new email arrives (V3)" operation
// and posts the event payload to the receiver Container App's
// /webhook endpoint.
//
// The trigger config is the last thing provisioned — it requires the
// receiver to already have a public URL (callbackUrl).

@description('Parent Connector Gateway resource name.')
param gatewayName string

@description('Trigger config name (2-64 chars).')
@minLength(2)
@maxLength(64)
param name string

@description('Description shown in the Connector Namespace portal.')
param triggerDescription string = 'On new Outlook email — POST to ACA receiver, which boots a sandbox per event.'

@description('Office 365 connection name (sibling connection-office365 module).')
param office365ConnectionName string

@description('Public callback URL (e.g. https://ca-receiver-xxx.region.azurecontainerapps.io/webhook).')
param callbackUrl string

@description('Outlook folder to watch. Default: Inbox.')
param folderPath string = 'Inbox'

@description('Outlook importance filter. Defaults to Any so every email fires the trigger and the sandbox decides what to act on.')
@allowed(['Any', 'Normal', 'High', 'Low'])
param importance string = 'Any'

resource gateway 'Microsoft.Web/connectorGateways@2026-05-01-preview' existing = {
  name: gatewayName
}

resource trigger 'Microsoft.Web/connectorGateways/triggerconfigs@2026-05-01-preview' = {
  parent: gateway
  name: name
  properties: {
    description: triggerDescription
    connectionDetails: {
      // Connector references in trigger configs use the connector's
      // swagger short name (lowercase) — `office365`, not `Office365`.
      // See the reference functions-connectors-net-builtinauth sample.
      connectorName: 'office365'
      connectionName: office365ConnectionName
    }
    // CamelCase swagger operationId — `OnNewEmailV3`, not the
    // friendly-name-with-parens form.
    operationName: 'OnNewEmailV3'
    parameters: [
      {
        name: 'folderPath'
        value: folderPath
      }
      {
        name: 'importance'
        value: importance
      }
    ]
    notificationDetails: {
      callbackUrl: callbackUrl
      httpMethod: 'POST'
    }
  }
}

output id string = trigger.id
output name string = trigger.name
