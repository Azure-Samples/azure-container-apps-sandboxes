#!/usr/bin/env bash
# Post-deploy hook for sandboxes-connectors-email-triage.
#
# Reads azd outputs and finishes the runtime wiring that Bicep
# deliberately deferred:
#
#   1. Issues an MCP-config-scoped, never-expiring runtime API key
#      from the Connector Gateway and stamps it onto the receiver
#      Container App as a secret + secretref env var. The sandbox
#      egress proxy uses it to add X-API-Key on outbound MCP calls.
#   2. Installs the experimental `connector-namespace` az CLI extension
#      if missing.
#   3. Authorizes both connections (Office 365, Teams) by invoking the
#      extension's `connection authorize` command, which pops a browser
#      tab per connection for OAuth consent.
#
# After this script finishes successfully, the trigger config (already
# provisioned by Bicep with callbackUrl pointing at the receiver) will
# start dispatching real email events at the receiver and the
# end-to-end flow is live.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

API_VERSION="2026-05-01-preview"
CONNECTOR_EXT_WHEEL="https://github.com/anthonychu/azure-cli-extensions/releases/download/connector-namespace-0.1.0/connector_namespace-0.1.0-py2.py3-none-any.whl"

echo -e "${YELLOW}==> Post-deploy: connector gateway wiring${NC}"

if ! command -v jq >/dev/null 2>&1; then
  echo -e "${RED}error: jq is required. Install it (apt/brew/choco) and re-run.${NC}" >&2
  exit 1
fi

# ---- 0. Inputs from azd outputs ------------------------------------------
outputs=$(azd env get-values --output json)

subscriptionId=$(echo "$outputs" | jq -r '.AZURE_SUBSCRIPTION_ID')
resourceGroupName=$(echo "$outputs" | jq -r '.resourceGroupName')
connectorGatewayName=$(echo "$outputs" | jq -r '.connectorGatewayName')
office365ConnectionName=$(echo "$outputs" | jq -r '.office365ConnectionName')
teamsConnectionName=$(echo "$outputs" | jq -r '.teamsConnectionName')
teamsMcpServerConfigName=$(echo "$outputs" | jq -r '.teamsMcpServerConfigName')
receiverContainerAppName=$(echo "$outputs" | jq -r '.receiverContainerAppName')

for v in subscriptionId resourceGroupName connectorGatewayName \
         office365ConnectionName teamsConnectionName \
         teamsMcpServerConfigName receiverContainerAppName; do
  if [[ -z "${!v}" || "${!v}" == "null" ]]; then
    echo -e "${RED}error: azd output '$v' missing. Did the deployment succeed?${NC}" >&2
    exit 1
  fi
done

ARM="https://management.azure.com/subscriptions/${subscriptionId}/resourceGroups/${resourceGroupName}/providers/Microsoft.Web/connectorGateways/${connectorGatewayName}"

# ---- 1. Fetch the runtime API key ----------------------------------------
echo -e "${YELLOW}==> Issuing MCP runtime API key (scoped to '${teamsMcpServerConfigName}', neverExpire)...${NC}"

keyBody=$(jq -nc \
  --arg scope "$teamsMcpServerConfigName" \
  '{ scope: $scope, neverExpire: true }')

keyResp=$(az rest \
  --method post \
  --uri "${ARM}/listApiKey?api-version=${API_VERSION}" \
  --body "$keyBody" \
  --headers Content-Type=application/json)

apiKey=$(echo "$keyResp" | jq -r '.key // empty')
if [[ -z "$apiKey" ]]; then
  echo -e "${RED}error: listApiKey returned no key. Response: ${keyResp}${NC}" >&2
  exit 1
fi
echo -e "${CYAN}    got key (length=${#apiKey})${NC}"

# ---- 2. Stamp it onto the receiver Container App -------------------------
echo -e "${YELLOW}==> Setting receiver secret + env (Container App restarts automatically)...${NC}"

az containerapp secret set \
  --resource-group "$resourceGroupName" \
  --name "$receiverContainerAppName" \
  --secrets "connector-gateway-api-key=${apiKey}" \
  --output none

az containerapp update \
  --resource-group "$resourceGroupName" \
  --name "$receiverContainerAppName" \
  --set-env-vars "CONNECTOR_GATEWAY_API_KEY=secretref:connector-gateway-api-key" \
  --output none

# ---- 2b. GitHub PAT (so Copilot CLI can talk to GitHub Models) ----------
githubPat=$(echo "$outputs" | jq -r '.GITHUB_PAT // empty')
if [[ -z "$githubPat" ]]; then
  githubPat="${GITHUB_PAT:-}"
fi
if [[ -z "$githubPat" ]]; then
  echo ""
  echo -e "${YELLOW}==> WARNING: GITHUB_PAT is not set.${NC}"
  echo -e "${YELLOW}    Copilot CLI in each sandbox will fail to auth to GitHub Models.${NC}"
  echo -e "${YELLOW}    Set it with:  azd env set GITHUB_PAT <your-classic-or-fine-grained-PAT>${NC}"
  echo -e "${YELLOW}    then re-run:  azd hooks run postdeploy${NC}"
else
  echo -e "${YELLOW}==> Setting GITHUB_PAT secret on receiver (length=${#githubPat})...${NC}"
  az containerapp secret set \
    --resource-group "$resourceGroupName" \
    --name "$receiverContainerAppName" \
    --secrets "github-pat=${githubPat}" \
    --output none
  az containerapp update \
    --resource-group "$resourceGroupName" \
    --name "$receiverContainerAppName" \
    --set-env-vars "GITHUB_PAT=secretref:github-pat" \
    --output none
fi

# ---- 2c. Optional Teams target (pre-pinned team + channel IDs) ----------
teamId=$(echo "$outputs" | jq -r '.TEAMS_TEAM_ID // empty')
channelId=$(echo "$outputs" | jq -r '.TEAMS_CHANNEL_ID // empty')
if [[ -n "$teamId" && -n "$channelId" ]]; then
  echo -e "${YELLOW}==> Setting TEAMS_TEAM_ID + TEAMS_CHANNEL_ID env on receiver...${NC}"
  az containerapp update \
    --resource-group "$resourceGroupName" \
    --name "$receiverContainerAppName" \
    --set-env-vars "TEAMS_TEAM_ID=$teamId" "TEAMS_CHANNEL_ID=$channelId" \
    --output none
else
  echo -e "${YELLOW}==> WARNING: TEAMS_TEAM_ID / TEAMS_CHANNEL_ID not set.${NC}"
  echo -e "${YELLOW}    Copilot will need to guess the Teams target in each tool call.${NC}"
  echo -e "${YELLOW}    Set with:  azd env set TEAMS_TEAM_ID <gid>; azd env set TEAMS_CHANNEL_ID <cid>${NC}"
fi

# ---- 3. Install the connector-namespace CLI extension --------------------
if ! az extension show --name connector-namespace >/dev/null 2>&1; then
  echo -e "${YELLOW}==> Installing experimental 'connector-namespace' az CLI extension...${NC}"
  az extension add --source "${CONNECTOR_EXT_WHEEL}" --yes
fi

# ---- 4. Authorize the two connections ------------------------------------
authorize_connection() {
  local conn_name="$1" label="$2"
  echo ""
  echo -e "${YELLOW}==> Authorizing ${label} (${conn_name})${NC}"
  echo -e "${CYAN}    A browser tab will open. Sign in with the M365 account whose ${label}${NC}"
  echo -e "${CYAN}    you want this connection to act on, then return to this terminal.${NC}"
  az connector-namespace connection authorize \
    --resource-group "$resourceGroupName" \
    --namespace-name "$connectorGatewayName" \
    --name "$conn_name"
}

authorize_connection "$office365ConnectionName" "Office 365 mailbox"
authorize_connection "$teamsConnectionName"     "Microsoft Teams channel"

# ---- 5. Done -------------------------------------------------------------
echo ""
echo -e "${GREEN}=============================================================${NC}"
echo -e "${GREEN} ALL DONE${NC}"
echo -e "${GREEN}=============================================================${NC}"
echo -e "${CYAN} Send an email to the consented mailbox and watch the receiver:${NC}"
echo -e "${CYAN}   az containerapp logs show -g ${resourceGroupName} \\${NC}"
echo -e "${CYAN}     -n ${receiverContainerAppName} --follow${NC}"
echo ""
echo -e "${CYAN} Tear down with:${NC}"
echo -e "${CYAN}   azd down --purge --force --no-prompt${NC}"
echo ""
