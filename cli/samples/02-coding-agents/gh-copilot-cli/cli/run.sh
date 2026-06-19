#!/usr/bin/env bash
# Coding agents — Copilot CLI inside a sandbox (CLI, portal-paste flow).
#
# Boots an ubuntu sandbox, installs Copilot CLI, applies a deny-default
# egress policy with three PAT-injection Transform rules whose
# Authorization values are the literal placeholder "PASTE_PAT_HERE".
# Then prints the sandboxes.azure.com link for the customer to drop in
# their PAT via the portal and run `copilot` from the portal's bash tab.
#
# The PAT never enters this script, the env, the shell, or any file on
# the operator's disk.
#
# Usage:
#   ./run.sh

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

# ---------- load samples/.env (env vars already set take precedence) ----------
dir="$here"
while [[ "$dir" != "/" && ! -f "$dir/.env" ]]; do dir="$(dirname "$dir")"; done
if [[ -f "$dir/.env" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" == \#* ]] && continue
    [[ -z "${!k:-}" ]] && export "$k=$v"
  done < "$dir/.env"
elif [[ -z "${ACA_SANDBOXGROUP_REGION:-}" && -z "${ACA_REGION:-}" ]]; then
  echo "error: samples/.env not found and no env vars set. Run setup first:" >&2
  echo "       python/samples/setup/setup.py  (or populate .env manually)" >&2
  exit 2
fi

# `aca` uses ACA_SUBSCRIPTION / ACA_REGION; setup writes the longer names.
export ACA_SUBSCRIPTION="${ACA_SUBSCRIPTION:-${AZURE_SUBSCRIPTION_ID:-}}"
export ACA_REGION="${ACA_REGION:-${ACA_SANDBOXGROUP_REGION:-}}"

SUB="${ACA_SUBSCRIPTION:?ACA_SUBSCRIPTION / AZURE_SUBSCRIPTION_ID not set}"
RG="${ACA_RESOURCE_GROUP:?ACA_RESOURCE_GROUP not set}"
SG="${ACA_SANDBOX_GROUP:?ACA_SANDBOX_GROUP not set}"

# ---------- requirements ----------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done
if [[ -z "$PY" ]]; then
  echo "error: neither 'python3' nor 'python' found in PATH" >&2
  exit 2
fi
for cmd in aca timeout; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required tool '$cmd' not found in PATH" >&2
    exit 2
  fi
done

# ---------- create sandbox ----------
RUN_ID="$("$PY" -c 'import uuid;print(uuid.uuid4().hex[:8])')"
ID=""

cleanup() {
  set +e
  if [[ -n "$ID" ]]; then
    echo "==> Deleting sandbox $ID..."
    aca sandbox delete --id "$ID" --yes >/dev/null 2>&1
  else
    # Interrupted before we got an ID — sweep by label.
    echo "==> Sweeping any leaked sandboxes (run=$RUN_ID)..."
    leaked=$(aca sandbox list -l "run=$RUN_ID" -o json 2>/dev/null \
      | "$PY" -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print("\n".join(s["id"] for s in (d if isinstance(d,list) else d.get("items",[]))))
except Exception: pass' || true)
    for sid in $leaked; do
      aca sandbox delete --id "$sid" --yes >/dev/null 2>&1 \
        && echo "    deleted leaked sandbox $sid"
    done
  fi
}
trap cleanup EXIT INT TERM HUP

echo "==> Booting sandbox (run=$RUN_ID)..."
aca sandbox create \
  --disk ubuntu --cpu 2000m --memory 4096Mi \
  --label "scenario=coding-agents" --label "run=$RUN_ID" \
  >/dev/null
ID=$(aca sandbox list -l "run=$RUN_ID" -o json \
  | "$PY" -c 'import sys,json
d=json.load(sys.stdin)
items=d if isinstance(d,list) else d.get("items",[])
print(items[0]["id"])')
echo "    sandbox: $ID"

# ---------- wait for exec readiness ----------
echo "==> Waiting for sandbox exec to come up..."
deadline=$(( $(date +%s) + 30 ))
while (( $(date +%s) < deadline )); do
  if aca sandbox exec --id "$ID" -c "true" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# ---------- install copilot (under default-allow egress) ----------
echo "==> Installing GitHub Copilot CLI..."
aca sandbox exec --id "$ID" \
  -c "timeout 180s bash -lc 'curl -fsSL https://gh.io/copilot-install | bash'"

# ---------- apply egress policy (placeholders only — no secrets) ----------
# Fail fast if the checked-in policy was edited to include a host rule
# that would short-circuit the Transform rules.
if grep -E '^\s*-\s*pattern:.*githubcopilot\.com' "$here/policy.yaml" >/dev/null; then
  echo "error: policy.yaml contains a host rule matching *.githubcopilot.com;" >&2
  echo "       this would disable PAT injection. Remove it." >&2
  exit 2
fi
echo "==> Applying egress policy (deny-default + GitHub allows + placeholder Transforms)..."
aca sandbox egress apply --id "$ID" --file "$here/policy.yaml" >/dev/null

URL="https://sandboxes.azure.com/sandbox-groups/$SUB/$RG/$SG/sandboxes/$ID"
echo
echo "========================================================================"
echo "Sandbox is ready. To finish setup, drop your GitHub PAT in the portal:"
echo
echo "  1. Open: $URL"
echo "  2. Click 'Egress Policy' (right-hand panel)."
echo "  3. For each of the 3 Transform rules, replace 'PASTE_PAT_HERE' in the"
echo "     Value field with your GitHub PAT. Keep the scheme prefix"
echo "     ('Bearer' or 'token'). Click Save."
echo "     -> Need a PAT? Run 'gh auth token' if you already use the gh CLI,"
echo "        or create a classic PAT at:"
echo "        https://github.com/settings/tokens/new?scopes=read:user,repo,workflow"
echo "  4. Once saved, come back to this terminal and press Enter — you'll"
echo "     drop into an interactive shell *inside* the sandbox. From there:"
echo "       copilot                            # start Copilot CLI"
echo "       curl -i https://api.github.com/user | head   # verify PAT injection"
echo "     Type 'exit' to leave the sandbox; this script will then delete it."
echo
echo "(Prefer the browser? The sandbox page in the portal has its own 'bash'"
echo " tab — you can use that instead and just press Enter here when done.)"
echo
echo "After paste: do NOT screenshot/share the Egress Policy panel"
echo "(the saved Values contain your PAT verbatim)."
echo "========================================================================"
echo
read -r -p "Press Enter to open an interactive shell in the sandbox... " _ || true

echo "==> Opening interactive shell (type 'exit' to leave and delete sandbox)..."
# `aca sandbox shell` opens an interactive TTY inside the sandbox. The
# customer can run `copilot` directly here; egress goes through the
# proxy with the pasted PAT injected on the wire. We intentionally don't
# pipe or capture the output — this is interactive.
aca sandbox shell --id "$ID" || true

echo "==> Done."
