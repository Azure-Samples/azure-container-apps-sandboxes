# Post-deploy hook for sandboxes-connectors-email-triage (Windows).
# Mirrors infra/scripts/postdeploy.sh — see that file for design notes.

$ErrorActionPreference = 'Stop'

$apiVersion = '2026-05-01-preview'
$connectorExtWheel = 'https://github.com/anthonychu/azure-cli-extensions/releases/download/connector-namespace-0.1.0/connector_namespace-0.1.0-py2.py3-none-any.whl'

Write-Host '==> Post-deploy: connector gateway wiring' -ForegroundColor Yellow

# ---- 0. Inputs from azd outputs ------------------------------------------
$outputs = azd env get-values --output json | ConvertFrom-Json

function Require-Output([string]$Name) {
    $val = $outputs.$Name
    if ([string]::IsNullOrEmpty($val)) {
        throw "azd output '$Name' missing. Did the deployment succeed?"
    }
    return $val
}

$subscriptionId            = Require-Output 'AZURE_SUBSCRIPTION_ID'
$resourceGroupName         = Require-Output 'resourceGroupName'
$connectorGatewayName      = Require-Output 'connectorGatewayName'
$office365ConnectionName   = Require-Output 'office365ConnectionName'
$teamsConnectionName       = Require-Output 'teamsConnectionName'
$teamsMcpServerConfigName  = Require-Output 'teamsMcpServerConfigName'
$receiverContainerAppName  = Require-Output 'receiverContainerAppName'

$arm = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$resourceGroupName/providers/Microsoft.Web/connectorGateways/$connectorGatewayName"

# ---- 1. Fetch the runtime API key ----------------------------------------
Write-Host "==> Issuing MCP runtime API key (scoped to '$teamsMcpServerConfigName', neverExpire)..." -ForegroundColor Yellow

# az CLI on Windows mangles inline JSON args when they contain quotes;
# write to a temp file and reference it with @file syntax.
$keyBody = @{ scope = $teamsMcpServerConfigName; neverExpire = $true } | ConvertTo-Json -Compress
$keyBodyFile = New-TemporaryFile
Set-Content -Path $keyBodyFile.FullName -Value $keyBody -Encoding utf8 -NoNewline
try {
    $keyRespJson = az rest --method post --uri "$arm/listApiKey?api-version=$apiVersion" `
        --body "@$($keyBodyFile.FullName)" --headers Content-Type=application/json
} finally {
    Remove-Item $keyBodyFile.FullName -Force -ErrorAction SilentlyContinue
}
$keyResp = $keyRespJson | ConvertFrom-Json
if (-not $keyResp.key) {
    throw "listApiKey returned no 'key'. Response: $keyRespJson"
}
$apiKey = $keyResp.key
Write-Host "    got key (length=$($apiKey.Length))" -ForegroundColor Cyan

# ---- 2. Stamp it onto the receiver Container App -------------------------
Write-Host '==> Setting receiver secret + env (Container App restarts automatically)...' -ForegroundColor Yellow

az containerapp secret set `
    --resource-group $resourceGroupName --name $receiverContainerAppName `
    --secrets "connector-gateway-api-key=$apiKey" --output none

az containerapp update `
    --resource-group $resourceGroupName --name $receiverContainerAppName `
    --set-env-vars "CONNECTOR_GATEWAY_API_KEY=secretref:connector-gateway-api-key" `
    --output none

# ---- 2b. GitHub PAT (so Copilot CLI can talk to GitHub Models) ----------
$githubPat = $outputs.GITHUB_PAT
if ([string]::IsNullOrEmpty($githubPat)) {
    $githubPat = [System.Environment]::GetEnvironmentVariable('GITHUB_PAT')
}
if ([string]::IsNullOrEmpty($githubPat)) {
    Write-Host ''
    Write-Host '==> WARNING: GITHUB_PAT is not set.' -ForegroundColor Yellow
    Write-Host '    Copilot CLI in each sandbox will fail to auth to GitHub Models.' -ForegroundColor Yellow
    Write-Host '    Set it with:  azd env set GITHUB_PAT <your-classic-or-fine-grained-PAT>' -ForegroundColor Yellow
    Write-Host '    then re-run:  azd hooks run postdeploy' -ForegroundColor Yellow
} else {
    Write-Host "==> Setting GITHUB_PAT secret on receiver (length=$($githubPat.Length))..." -ForegroundColor Yellow
    az containerapp secret set `
        --resource-group $resourceGroupName --name $receiverContainerAppName `
        --secrets "github-pat=$githubPat" --output none
    az containerapp update `
        --resource-group $resourceGroupName --name $receiverContainerAppName `
        --set-env-vars "GITHUB_PAT=secretref:github-pat" --output none
}

# ---- 2c. Optional Teams target (pre-pinned team + channel IDs) ----------
$teamId    = $outputs.TEAMS_TEAM_ID
$channelId = $outputs.TEAMS_CHANNEL_ID
if (-not [string]::IsNullOrEmpty($teamId) -and -not [string]::IsNullOrEmpty($channelId)) {
    Write-Host '==> Setting TEAMS_TEAM_ID + TEAMS_CHANNEL_ID env on receiver...' -ForegroundColor Yellow
    az containerapp update `
        --resource-group $resourceGroupName --name $receiverContainerAppName `
        --set-env-vars "TEAMS_TEAM_ID=$teamId" "TEAMS_CHANNEL_ID=$channelId" --output none
} else {
    Write-Host '==> WARNING: TEAMS_TEAM_ID / TEAMS_CHANNEL_ID not set.' -ForegroundColor Yellow
    Write-Host '    Copilot will need to guess the Teams target in each tool call.' -ForegroundColor Yellow
    Write-Host '    Set with:  azd env set TEAMS_TEAM_ID <gid>; azd env set TEAMS_CHANNEL_ID <cid>' -ForegroundColor Yellow
}

# ---- 3. Install the connector-namespace CLI extension --------------------
$ext = az extension show --name connector-namespace 2>$null
if (-not $ext) {
    Write-Host "==> Installing experimental 'connector-namespace' az CLI extension..." -ForegroundColor Yellow
    az extension add --source $connectorExtWheel --yes
}

# ---- 4. Authorize the two connections ------------------------------------
function Authorize-Connection([string]$ConnName, [string]$Label) {
    Write-Host ''
    Write-Host "==> Authorizing $Label ($ConnName)" -ForegroundColor Yellow
    Write-Host "    A browser tab will open. Sign in with the M365 account whose $Label" -ForegroundColor Cyan
    Write-Host '    you want this connection to act on, then return to this terminal.' -ForegroundColor Cyan
    az connector-namespace connection authorize `
        --resource-group $resourceGroupName `
        --namespace-name $connectorGatewayName `
        --name $ConnName
}

Authorize-Connection $office365ConnectionName 'Office 365 mailbox'
Authorize-Connection $teamsConnectionName     'Microsoft Teams channel'

# ---- 5. Done -------------------------------------------------------------
Write-Host ''
Write-Host '=============================================================' -ForegroundColor Green
Write-Host ' ALL DONE' -ForegroundColor Green
Write-Host '=============================================================' -ForegroundColor Green
Write-Host ' Send an email to the consented mailbox and watch the receiver:' -ForegroundColor Cyan
Write-Host "   az containerapp logs show -g $resourceGroupName \"
Write-Host "     -n $receiverContainerAppName --follow"
Write-Host ''
Write-Host ' Tear down with:' -ForegroundColor Cyan
Write-Host '   azd down --purge --force --no-prompt' -ForegroundColor Cyan
Write-Host ''
