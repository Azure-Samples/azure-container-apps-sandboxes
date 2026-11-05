#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "sandbox-setup-test-$([Guid]::NewGuid().ToString('N'))"
$setupDirectory = Join-Path $testRoot 'setup'
$samplesDirectory = Join-Path $testRoot 'samples'
$setupFile = Join-Path $setupDirectory 'setup.ps1'
$envFile = Join-Path $samplesDirectory '.env'
$testState = @{
    RoleExists = $false
    FailGroup = $false
    RoleCreates = 0
    RestPuts = 0
    Calls = [System.Collections.Generic.List[string]]::new()
}

function az {
    $command = $args -join ' '
    $testState.Calls.Add($command)
    $global:LASTEXITCODE = 0
    switch -Regex ($command) {
        '^account show ' { return 'test-subscription' }
        '^ad signed-in-user show ' { return 'test-user' }
        '^group create ' {
            if ($testState.FailGroup) { $global:LASTEXITCODE = 1 }
            return
        }
        '^rest --method put ' { $testState.RestPuts++; return }
        '^rest --method get ' { return 'Succeeded' }
        '^role assignment list ' {
            if ($testState.RoleExists) {
                return '[{"principalId":"test-user","roleDefinitionId":"/roles/c24cf47c-5077-412d-a19c-45202126392c","scope":"/subscriptions/test-subscription/resourceGroups/aca-sandboxes-dotnet-samples-rg/providers/Microsoft.App/sandboxGroups/aca-sandboxes-dotnet-samples"}]'
            }
            return '[]'
        }
        '^role assignment create ' { $testState.RoleCreates++; return }
        default { throw "Unexpected Azure CLI command: $command" }
    }
}

try {
    New-Item -ItemType Directory -Path $setupDirectory, $samplesDirectory | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\setup\setup.ps1') -Destination $setupFile
    & $setupFile -PrincipalId 'test-user'
    $config = Get-Content -LiteralPath $envFile -Raw
    if ($config -notmatch 'AZURE_SUBSCRIPTION_ID=test-subscription' -or $testState.RoleCreates -ne 1) {
        throw 'Setup did not write configuration or create the role assignment.'
    }

    $testState.RoleExists = $true
    & $setupFile -PrincipalId 'test-user'
    if ($testState.RoleCreates -ne 1 -or $testState.RestPuts -ne 2) {
        throw 'Repeated setup should reuse the existing role assignment.'
    }
    if (@($testState.Calls | Where-Object { $_ -match '^account set ' }).Count -gt 0) {
        throw 'Setup must not change the globally active subscription.'
    }

    Remove-Item -LiteralPath $envFile
    $testState.FailGroup = $true
    $failed = $false
    try { & $setupFile -PrincipalId 'test-user' }
    catch { $failed = $true }
    if (-not $failed -or (Test-Path -LiteralPath $envFile)) {
        throw 'A failed Azure command must stop setup before writing configuration.'
    }
    Write-Host 'Setup checks passed: configuration, repeatability, and fail-fast behavior. No Azure calls made.'
}
finally {
    if (Test-Path -LiteralPath $envFile) { Remove-Item -LiteralPath $envFile }
    if (Test-Path -LiteralPath $setupFile) { Remove-Item -LiteralPath $setupFile }
    foreach ($directory in @($setupDirectory, $samplesDirectory, $testRoot)) {
        if (Test-Path -LiteralPath $directory) { Remove-Item -LiteralPath $directory }
    }
}
