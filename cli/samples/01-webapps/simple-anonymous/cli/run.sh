#!/usr/bin/env bash
# Simple anonymous web app in a sandbox (aca CLI).
#
# Creates a sandbox on node-22, uploads the Node app from ../app/, starts it
# on :8080, exposes the port anonymously (open to the internet), and verifies
# both in-sandbox and host-side responses.

set -euo pipefail

# git-bash on Windows rewrites absolute POSIX paths (like `/app/server.js`) into
# Windows paths before passing them to non-POSIX binaries. Suppress that so the
# `--path /app/...` arguments to `aca` reach the sandbox unchanged.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

here="$(cd "$(dirname "$0")" && pwd)"
dir="$here"
while [[ "$dir" != "/" && ! -f "$dir/.env" ]]; do
    dir="$(dirname "$dir")"
done
if [[ -f "$dir/.env" ]]; then
    set -a; . "$dir/.env"; set +a
else
    echo "error: could not find samples/.env - run setup/cli/setup.sh first?" >&2
    exit 1
fi

DISK="${ACA_WEBAPP_DISK:-node-22}"
PORT=8080
APP_DIR="$here/../app"

# Convert a path for the aca CLI. On Windows + git-bash, `aca.exe` expects a
# native Windows path (cygpath -w). Elsewhere, pass through.
to_native() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}

# Extract a top-level string field from a JSON blob on stdin. The blob may be
# an object or a one-element array (aca CLI returns arrays for some commands).
# No hard dependency on jq or python3.
json_field() {
    local field="$1"
    if command -v jq >/dev/null 2>&1; then
        jq -r "if type==\"array\" then .[0].${field} else .${field} end // empty"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import sys,json; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d.get('${field}',''))"
    elif command -v python >/dev/null 2>&1; then
        python -c "import sys,json; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d.get('${field}',''))"
    else
        sed -n 's/.*"'"$field"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1
    fi
}

echo "==> Creating sandbox (disk=$DISK)..."
CREATE_OUTPUT="$(aca sandbox create --disk "$DISK")"
SANDBOX_ID="$(echo "$CREATE_OUTPUT" | sed -n 's/^Created sandbox: //p' | tail -n1)"
[[ -n "$SANDBOX_ID" ]] || { echo "error: could not parse sandbox id" >&2; exit 1; }
echo "    sandbox: $SANDBOX_ID"

PORT_ADDED=0
cleanup() {
    if [[ "$PORT_ADDED" == "1" ]]; then
        echo "==> aca sandbox port remove --port $PORT"
        aca sandbox port remove --id "$SANDBOX_ID" --port "$PORT" >/dev/null 2>&1 || \
          echo "    warning: port remove failed"
    fi
    echo "==> Deleting sandbox $SANDBOX_ID..."
    aca sandbox delete --id "$SANDBOX_ID" --yes >/dev/null || true
}
trap cleanup EXIT

echo "==> Uploading app files..."
aca sandbox exec --id "$SANDBOX_ID" -c "mkdir -p /app" >/dev/null
aca sandbox fs write --id "$SANDBOX_ID" --path /app/server.js   --file "$(to_native "$APP_DIR/server.js")"   >/dev/null
aca sandbox fs write --id "$SANDBOX_ID" --path /app/package.json --file "$(to_native "$APP_DIR/package.json")" >/dev/null

echo "==> Starting Node server on :$PORT..."
aca sandbox exec --id "$SANDBOX_ID" -c \
  "cd /app && nohup node server.js > /tmp/node.log 2>&1 &" >/dev/null

echo "==> Polling in-sandbox readiness on /healthz..."
for i in $(seq 1 30); do
    code="$(aca sandbox exec --id "$SANDBOX_ID" -c \
      "curl -fsS -o /dev/null -w '%{http_code}' http://localhost:$PORT/healthz || true" 2>/dev/null | tail -n1 | tr -d '[:space:]')"
    if [[ "$code" == "200" ]]; then break; fi
    sleep 1
done
if [[ "$code" != "200" ]]; then
    echo "error: server not ready (last code=$code)" >&2
    aca sandbox exec --id "$SANDBOX_ID" -c "cat /tmp/node.log" >&2 || true
    exit 1
fi
echo "    server is ready"

echo "==> aca sandbox port add --port $PORT --anonymous"
PORT_OUTPUT="$(aca sandbox port add --id "$SANDBOX_ID" --port "$PORT" --anonymous -o json)"
PORT_ADDED=1
URL="$(echo "$PORT_OUTPUT" | json_field url)"
[[ -n "$URL" ]] || { echo "error: no URL in port add response" >&2; exit 1; }
echo "    public URL: $URL"

echo "==> Verifying public URL (host-side)..."
deadline=$(( $(date +%s) + 60 ))
while :; do
    if body="$(curl -fsS --max-time 10 "$URL/healthz" 2>/dev/null)"; then
        echo "    GET /healthz -> $body"
        break
    fi
    [[ $(date +%s) -lt $deadline ]] || { echo "error: public URL not ready" >&2; exit 1; }
    sleep 2
done

for path in "/api/hello" "/api/info"; do
    body="$(curl -fsS --max-time 10 "$URL$path")"
    echo "    GET $path -> $body"
done

# HTML landing page smoke check.
# Use a cwd-relative file so it works on both POSIX and Windows git-bash
# (where MSYS_NO_PATHCONV=1 above means absolute /tmp paths get passed
# literally to Windows curl, which cannot resolve them).
landing_file="./landing-tmp.html"
html_code="$(curl -s -o "$landing_file" -w '%{http_code}' --max-time 10 "$URL/")"
html_bytes="$(wc -c < "$landing_file" | tr -d ' ')"
if [[ "$html_code" == "200" ]] && grep -q "Hello from a sandbox" "$landing_file"; then
    echo "    GET /           -> http 200 (HTML, $html_bytes bytes)"
    rm -f "$landing_file"
else
    rm -f "$landing_file"
    echo "error: landing page check failed (code=$html_code, bytes=$html_bytes)" >&2
    exit 1
fi

# Shape sanity check — try jq if present, fall back to python, else skip.
if command -v jq >/dev/null 2>&1; then
    curl -fsS "$URL/healthz" | jq -e '.status == "ok"' >/dev/null
    curl -fsS "$URL/api/hello" | jq -e '.message == "Hello from sandbox" and has("hostname") and has("uptime")' >/dev/null
    curl -fsS "$URL/api/info" | jq -e 'has("node") and has("platform")' >/dev/null
    echo "==> All endpoint shape assertions passed."
elif command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
    PY=$(command -v python3 || command -v python)
    curl -fsS "$URL/healthz" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok', d"
    curl -fsS "$URL/api/hello" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('message')=='Hello from sandbox', d; assert 'hostname' in d and 'uptime' in d"
    curl -fsS "$URL/api/info" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert 'node' in d and 'platform' in d, d"
    echo "==> All endpoint shape assertions passed."
else
    echo "    (skipping JSON shape asserts: no jq or python on PATH)"
fi
echo "==> Done."
