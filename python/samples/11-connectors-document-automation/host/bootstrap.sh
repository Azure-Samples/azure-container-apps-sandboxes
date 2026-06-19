#!/usr/bin/env bash
# bootstrap.sh
#
# Run ONCE inside the host sandbox (uploaded + executed by the
# post-deploy script after `azd up`). Installs the document
# automation toolchain, the Python listener, and brings it up as a
# long-running uvicorn process on :8080.
#
# Idempotent — running it twice is a no-op (apt cached, pip cached,
# old uvicorn killed before re-launch). The post-deploy script
# uploads this and runs it via `sandbox.exec` once at deploy time.
#
# Required env vars (passed by the post-deploy script before invoking
# this — they end up written to /opt/listener/.env which the listener
# loads at start-up; secrets stay out of /proc/N/cmdline):
#
#   SHAREPOINT_MCP_URL       full HTTPS URL of the namespace-fronted MCP
#   SHAREPOINT_SITE_URL      e.g. https://contoso.sharepoint.com/teams/Finance
#   SHAREPOINT_LIBRARY_ID    GUID of the SharePoint library
#   SHAREPOINT_OUTPUT_FOLDER folder name within the library for results
#   COPILOT_GITHUB_TOKEN     PAT for Copilot CLI -> GitHub Models

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}==> bootstrap: installing document automation toolchain${NC}"

export DEBIAN_FRONTEND=noninteractive

# ---- 1. apt packages ----------------------------------------------------
apt-get update -qq
apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    >/dev/null

# ---- 2. Python venv + listener deps -------------------------------------
echo -e "${YELLOW}==> creating /opt/listener venv + installing deps${NC}"

mkdir -p /opt/listener /work
python3 -m venv /opt/listener/.venv
# shellcheck disable=SC1091
. /opt/listener/.venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r /opt/listener/requirements.txt

# OCR / PDF helpers the AGENT may also use directly via python3 in
# /opt/listener/.venv — install them here so they're warmed up.
pip install --quiet pdfplumber pytesseract pillow

deactivate

# ---- 3. Copilot CLI -----------------------------------------------------
if ! command -v copilot >/dev/null 2>&1; then
    echo -e "${YELLOW}==> installing GitHub Copilot CLI${NC}"
    curl -fsSL https://gh.io/copilot-install | bash
fi

# Make Copilot's install location discoverable from the listener's
# environment (the installer typically drops a binary in /root/.local/bin
# or /usr/local/bin — pin both on PATH in start.sh below).
COPILOT_PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin"

# ---- 4. Launch listener as a detached process ---------------------------
# Sandboxes don't run systemd. We use setsid to fully detach so the
# listener survives the bootstrap exec session terminating (the
# sandbox SDK's `exec` kills the entire process group on disconnect,
# so a plain `nohup ... &` isn't enough — setsid puts the listener
# in its own session and process group).
#
# Idempotent: kill any old instance first.

pkill -f 'uvicorn listener:app' 2>/dev/null || true
sleep 1

# Write the env file the listener reads.
mkdir -p /opt/listener /var/log
cat >/opt/listener/.env <<EOF
SHAREPOINT_MCP_URL=${SHAREPOINT_MCP_URL}
SHAREPOINT_SITE_URL=${SHAREPOINT_SITE_URL:-}
SHAREPOINT_LIBRARY_ID=${SHAREPOINT_LIBRARY_ID:-}
SHAREPOINT_INPUT_FOLDER=${SHAREPOINT_INPUT_FOLDER:-}
SHAREPOINT_OUTPUT_FOLDER=${SHAREPOINT_OUTPUT_FOLDER:-Extracted}
COPILOT_GITHUB_TOKEN=${COPILOT_GITHUB_TOKEN:-}
EOF
chmod 600 /opt/listener/.env

# Write the start script. We use this both at first bootstrap AND
# any time we hot-reload the listener.
cat >/opt/listener/start.sh <<'STARTEOF'
#!/usr/bin/env bash
set -e
pkill -f 'uvicorn listener:app' 2>/dev/null || true
sleep 1
cd /opt/listener
set -a; . /opt/listener/.env; set +a
export PATH=/opt/listener/.venv/bin:/root/.local/bin:/usr/local/bin:/usr/bin:/bin
exec /opt/listener/.venv/bin/uvicorn listener:app --host 0.0.0.0 --port 8080 \
    >>/var/log/listener.log 2>&1 </dev/null
STARTEOF
chmod +x /opt/listener/start.sh

nohup setsid /opt/listener/start.sh >/var/log/listener.start.log 2>&1 < /dev/null &
disown

# ---- 5. Wait for /healthz before returning success ----------------------
echo -e "${YELLOW}==> waiting for listener /healthz...${NC}"
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
        echo -e "${GREEN}listener is up (took ${i}s)${NC}"
        exit 0
    fi
    sleep 1
done

echo -e "${RED}error: listener never became healthy. last 100 lines of /var/log/listener.log:${NC}" >&2
tail -n 100 /var/log/listener.log >&2 || true
exit 1
