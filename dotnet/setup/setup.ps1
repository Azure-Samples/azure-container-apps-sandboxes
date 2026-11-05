#Requires -Version 7.0
[CmdletBinding()]
param(
    [string] $SubscriptionId,
    [string] $ResourceGroup = 'aca-sandboxes-dotnet-samples-rg',
    [string] $SandboxGroup = 'aca-sandboxes-dotnet-samples',
    [string] $Region = 'eastus2',
    [string] $PrincipalId = $env:ACA_PRINCIPAL_ID,
    [ValidateSet('User', 'ServicePrincipal', 'Group')]
    [string] $PrincipalType = 'User'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Az {
    param([string[]] $Arguments)
    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: az $($Arguments -join ' ')"
    }
    return $result
}

if (-not $SubscriptionId) {
    $SubscriptionId = Invoke-Az -Arguments @('account', 'show', '--query', 'id', '-o', 'tsv')
}
if (-not $PrincipalId) {
    $PrincipalId = Invoke-Az -Arguments @('ad', 'signed-in-user', 'show', '--query', 'id', '-o', 'tsv')
}

$scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.App/sandboxGroups/$SandboxGroup"
$roleId = 'c24cf47c-5077-412d-a19c-45202126392c'

Write-Host "Creating resource group $ResourceGroup in $Region..."
Invoke-Az -Arguments @(
    'group', 'create', '--subscription', $SubscriptionId,
    '--name', $ResourceGroup, '--location', $Region, '-o', 'none'
) | Out-Null

$bodyFile = [System.IO.Path]::GetTempFileName()
try {
    @{ location = $Region; properties = @{} } |
        ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $bodyFile -Encoding utf8
    Write-Host "Creating sandbox group $SandboxGroup..."
    Invoke-Az -Arguments @(
        'rest', '--method', 'put',
        '--url', "https://management.azure.com${scope}?api-version=2026-02-01-preview",
        '--body', "@$bodyFile", '-o', 'none'
    ) | Out-Null
}
finally {
    Remove-Item -LiteralPath $bodyFile
}

$deadline = [DateTime]::UtcNow.AddMinutes(10)
do {
    $state = Invoke-Az -Arguments @(
        'rest', '--method', 'get',
        '--url', "https://management.azure.com${scope}?api-version=2026-02-01-preview",
        '--query', 'properties.provisioningState', '-o', 'tsv'
    )
    if ($state -eq 'Succeeded') { break }
    if ($state -in @('Failed', 'Canceled')) {
        throw "Sandbox group provisioning entered state $state."
    }
    if ([DateTime]::UtcNow -ge $deadline) {
        throw "Timed out provisioning $SandboxGroup."
    }
    Start-Sleep -Seconds 3
} while ($true)

Write-Host 'Granting Container Apps SandboxGroup Data Owner...'
$assignments = Invoke-Az -Arguments @(
    'role', 'assignment', 'list', '--subscription', $SubscriptionId,
    '--scope', $scope, '--all', '-o', 'json'
)
$existing = @($assignments | ConvertFrom-Json | Where-Object {
    $_.principalId -eq $PrincipalId -and
    $_.roleDefinitionId -like "*/$roleId" -and $_.scope -eq $scope
})
if ($existing.Count -eq 0) {
    Invoke-Az -Arguments @(
        'role', 'assignment', 'create', '--subscription', $SubscriptionId,
        '--assignee-object-id', $PrincipalId, '--assignee-principal-type', $PrincipalType,
        '--role', $roleId, '--scope', $scope, '-o', 'none'
    ) | Out-Null
}

$envFile = Join-Path $PSScriptRoot '..\samples\.env'
@(
    "AZURE_SUBSCRIPTION_ID=$SubscriptionId"
    "ACA_RESOURCE_GROUP=$ResourceGroup"
    "ACA_SANDBOX_GROUP=$SandboxGroup"
    "ACA_SANDBOXGROUP_REGION=$Region"
) | Set-Content -LiteralPath $envFile -Encoding utf8
Write-Host "Wrote $envFile. Allow 30-60 seconds for RBAC propagation."
