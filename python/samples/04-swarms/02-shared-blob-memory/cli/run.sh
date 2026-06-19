#!/usr/bin/env bash
# Shared-blob memory swarm — aca CLI variant.
#
# Same shape as 01-sandbox-inception/cli, plus: the worker group owns an
# AzureBlob volume that every worker and the aggregator mount at
# /mnt/shared. Workers checkpoint JSON to it; an aggregator sandbox
# (spawned after the workers exit) globs and aggregates. The platform
# brokers identity/storage behind the mount — no `azure-storage-blob`,
# no SAS, no extra role grants.
#
# The same `aca config` ergonomics from variant 01 apply: neither the
# host nor the orchestrator passes `--subscription / --resource-group /
# --group / --managed-identity` on individual `aca` calls.
#
# Reads samples/.env (written by setup/python/setup.py or
# setup/cli/setup.sh).

set -uo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# ---------------- 0. Source samples/.env ----------------
dir="$(cd "$(dirname "$0")" && pwd)"
while [[ "$dir" != "/" && ! -f "$dir/.env" ]]; do
    dir="$(dirname "$dir")"
done
if [[ -f "$dir/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$dir/.env"
    set +a
else
    echo "error: could not find samples/.env — run setup/cli/setup.sh first" >&2
    exit 1
fi

ROLE_NAME="Container Apps SandboxGroup Data Owner"
CLI_INSTALL_URL="https://raw.githubusercontent.com/microsoft/azure-container-apps/main/docs/early/aca-cli/install.sh"
WORKERS=4
DARTS_PER_WORKER=1000000
SUFFIX="$(printf '%08x' "$(( (RANDOM<<15) ^ RANDOM ^ ($(date +%s) & 0xffff) ))")"
ORCH_GROUP="swarm-orch-$SUFFIX"
WORKER_GROUP="swarm-workers-$SUFFIX"
VOLUME_NAME="shared-memory"
RUN_ID="$SUFFIX"
ORIGINAL_SANDBOX_GROUP="${ACA_SANDBOX_GROUP:-}"
ORCH_ID=""

cleanup() {
    set +e
    if [[ -n "$ORCH_ID" ]]; then
        echo "==> Deleting orchestrator sandbox $ORCH_ID..."
        aca config sandbox set --group "$ORCH_GROUP" --region "$ACA_SANDBOXGROUP_REGION" >/dev/null 2>&1
        aca sandbox delete --id "$ORCH_ID" --yes >/dev/null 2>&1
    fi
    echo "==> Deleting volume $VOLUME_NAME from $WORKER_GROUP..."
    aca config sandbox set --group "$WORKER_GROUP" --region "$ACA_SANDBOXGROUP_REGION" >/dev/null 2>&1
    aca sandboxgroup volume delete --name "$VOLUME_NAME" --yes >/dev/null 2>&1
    for grp in "$ORCH_GROUP" "$WORKER_GROUP"; do
        echo "==> Deleting sandbox group $grp..."
        aca sandboxgroup delete --name "$grp" --yes >/dev/null 2>&1
    done
    if [[ -n "$ORIGINAL_SANDBOX_GROUP" ]]; then
        echo "==> Restoring original aca config sandbox group ($ORIGINAL_SANDBOX_GROUP)..."
        aca config sandbox set --group "$ORIGINAL_SANDBOX_GROUP" >/dev/null 2>&1
    fi
}
trap cleanup EXIT

# ---------------- 1. Provision orchestrator group with MI ----------------
echo "==> Provisioning orchestrator group $ORCH_GROUP with SystemAssigned MI..."
aca sandboxgroup create \
    --name "$ORCH_GROUP" \
    --location "$ACA_SANDBOXGROUP_REGION" >/dev/null

aca config sandbox set --group "$ORCH_GROUP" --region "$ACA_SANDBOXGROUP_REGION" >/dev/null

aca sandboxgroup identity assign --name "$ORCH_GROUP" --system-assigned >/dev/null

PRINCIPAL_ID="$(aca sandboxgroup identity show --name "$ORCH_GROUP" -o json \
    | grep -oE '"principalId"[^"]*"[0-9a-fA-F-]+"' \
    | grep -oE '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')"
if [[ -z "$PRINCIPAL_ID" ]]; then
    echo "error: orchestrator group has no principalId — MI not enabled?" >&2
    exit 1
fi
echo "    principalId: $PRINCIPAL_ID"

# ---------------- 2. Worker group + role grant + volume ----------------
echo "==> Provisioning worker group $WORKER_GROUP..."
aca sandboxgroup create \
    --name "$WORKER_GROUP" \
    --location "$ACA_SANDBOXGROUP_REGION" >/dev/null

echo "==> Granting '$ROLE_NAME' on $WORKER_GROUP → orchestrator MI..."
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if aca sandboxgroup role create \
            --role "$ROLE_NAME" \
            --principal-id "$PRINCIPAL_ID" \
            --name "$WORKER_GROUP" 2>/tmp/role.err; then
        break
    fi
    if grep -q "RoleAssignmentExists\|already exists" /tmp/role.err 2>/dev/null; then
        echo "    role already assigned"
        break
    fi
    if [[ "$attempt" -eq 10 ]]; then
        cat /tmp/role.err >&2
        echo "error: role grant failed after $attempt attempts" >&2
        exit 1
    fi
    echo "    attempt $attempt: principal not yet replicated, retrying in 10s..."
    sleep 10
done
rm -f /tmp/role.err

echo "==> Waiting 20s for RBAC propagation..."
sleep 20

echo "==> Creating AzureBlob volume '$VOLUME_NAME' on $WORKER_GROUP..."
# Volume creation uses the host's `az login` against the worker group;
# the orchestrator MI will later mount it (Data Owner covers both).
# Briefly flip context to the worker group, create, then flip back so
# the rest of the host script keeps targeting the orchestrator group.
aca config sandbox set --group "$WORKER_GROUP" --region "$ACA_SANDBOXGROUP_REGION" >/dev/null
aca sandboxgroup volume create --name "$VOLUME_NAME" --type AzureBlob >/dev/null
aca config sandbox set --group "$ORCH_GROUP" --region "$ACA_SANDBOXGROUP_REGION" >/dev/null

# ---------------- 3. Orchestrator sandbox ----------------
echo "==> Creating orchestrator sandbox (disk=ubuntu) in $ORCH_GROUP..."
CREATE_OUT="$(aca sandbox create --disk ubuntu --label swarm=shared-blob --label role=orchestrator)"
echo "$CREATE_OUT"
ORCH_ID="$(printf '%s\n' "$CREATE_OUT" | sed -n 's/^Created sandbox: //p' | tail -n1)"
if [[ -z "$ORCH_ID" ]]; then
    echo "error: could not parse orchestrator sandbox id" >&2
    exit 1
fi

# ---------------- 4. Bootstrap orchestrator (install aca + upload swarm.sh) ----------------
echo "==> Installing aca CLI inside orchestrator..."
INSTALL_OUT="$(aca sandbox exec --id "$ORCH_ID" -c "curl -fsSL $CLI_INSTALL_URL | sh" 2>&1)"
echo "$INSTALL_OUT" | tail -5
if ! grep -q "successfully" <<< "$INSTALL_OUT" && ! aca sandbox exec --id "$ORCH_ID" -c "which aca && aca --version" >/dev/null 2>&1; then
    echo "error: aca install inside orchestrator failed" >&2
    echo "$INSTALL_OUT" >&2
    exit 1
fi

SWARM_SH="$(mktemp)"
cat > "$SWARM_SH" <<'INNER_EOF'
#!/usr/bin/env bash
# Runs INSIDE the orchestrator sandbox. Fans out N worker sandboxes in
# the WORKER group, mounts the shared AzureBlob volume into each at
# /mnt/shared, has each worker write a JSON checkpoint, deletes the
# workers, then spawns ONE aggregator sandbox that mounts the same
# volume and reads all checkpoints back. The platform handles every
# byte of storage / identity plumbing under that mount.

set -uo pipefail
ACA="$(command -v aca || echo /usr/local/bin/aca)"

echo "--- aca auth status (orchestrator, MI) ---"
"$ACA" auth status || true

echo "--- env-based config (worker context) ---"
echo "ACA_SUBSCRIPTION=$ACA_SUBSCRIPTION"
echo "ACA_RESOURCE_GROUP=$ACA_RESOURCE_GROUP"
echo "ACA_SANDBOX_GROUP=$ACA_SANDBOX_GROUP"
echo "ACA_SANDBOX_MANAGED_IDENTITY=$ACA_SANDBOX_MANAGED_IDENTITY"
echo "ACA_REGION=$ACA_REGION"
echo "VOLUME_NAME=$VOLUME_NAME  RUN_ID=$RUN_ID  WORKERS=$WORKERS  DARTS=$DARTS"

# Worker writes /mnt/shared/run-$RUN_ID/worker-$i.json with the pi sample.
# Atomic write (tmp + mv) so an aggregator never sees a partial file.
WORKER_SCRIPT='set -e
mkdir -p /mnt/shared/run-'$RUN_ID'
python3 - <<PY
import json, os, random, sys, time
i = int(sys.argv[1]); n = int(sys.argv[2])
inside = sum(1 for _ in range(n) if random.random()**2 + random.random()**2 < 1.0)
path = f"/mnt/shared/run-'$RUN_ID'/worker-{i}.json"
tmp  = path + ".tmp"
with open(tmp, "w") as f:
    json.dump({"worker": i, "inside": inside, "total": n, "ts": time.time()}, f)
os.replace(tmp, path)
print(f"WORKER_DONE i={i} inside={inside} total={n}")
PY'

worker_run() {
    local i="$1"
    local out="/tmp/worker_${i}.out"
    local t0 t1 dt id create_out exec_out
    t0=$(date +%s.%N)
    create_out="$("$ACA" sandbox create --disk ubuntu --label worker=$i 2>&1)"
    id="$(printf '%s\n' "$create_out" | sed -n 's/^Created sandbox: //p' | tail -n1)"
    if [[ -z "$id" ]]; then
        echo "WORKER_ERROR $i create_failed" > "$out"
        return
    fi
    "$ACA" sandbox mount --id "$id" --volume "$VOLUME_NAME" --path /mnt/shared >/dev/null 2>&1
    exec_out="$("$ACA" sandbox exec --id "$id" -c "$WORKER_SCRIPT $i $DARTS" 2>&1)"
    t1=$(date +%s.%N)
    dt=$(awk "BEGIN{printf \"%.2f\", $t1 - $t0}")
    echo "WORKER_RESULT $i $id ELAPSED_S=$dt" > "$out"
    printf '%s\n' "$exec_out" | grep -E '^WORKER_DONE' >> "$out"
    "$ACA" sandbox delete --id "$id" --yes >/dev/null 2>&1 || true
}

echo "--- spawning $WORKERS workers in $ACA_SANDBOX_GROUP via MI ---"
for i in $(seq 0 $((WORKERS-1))); do
    worker_run "$i" &
done
wait

for i in $(seq 0 $((WORKERS-1))); do
    cat "/tmp/worker_${i}.out"
done

# --- aggregator: separate sandbox, same group, same mount ---
echo "--- spawning aggregator (mounts same volume after workers are gone) ---"
agg_create="$("$ACA" sandbox create --disk ubuntu --label role=aggregator 2>&1)"
AGG_ID="$(printf '%s\n' "$agg_create" | sed -n 's/^Created sandbox: //p' | tail -n1)"
if [[ -z "$AGG_ID" ]]; then
    echo "AGGREGATOR_ERROR create_failed"
    echo "$agg_create"
    exit 1
fi
"$ACA" sandbox mount --id "$AGG_ID" --volume "$VOLUME_NAME" --path /mnt/shared >/dev/null 2>&1

AGG_SCRIPT='python3 - <<PY
import glob, json
paths = sorted(glob.glob("/mnt/shared/run-'$RUN_ID'/worker-*.json"))
print(f"AGGREGATED_FILES={len(paths)}")
inside = total = 0
for p in paths:
    d = json.load(open(p))
    inside += d["inside"]; total += d["total"]
    print(f"  {p}: inside={d[\"inside\"]} total={d[\"total\"]}")
print(f"RESULT INSIDE={inside} TOTAL={total}")
PY'
"$ACA" sandbox exec --id "$AGG_ID" -c "$AGG_SCRIPT"
"$ACA" sandbox delete --id "$AGG_ID" --yes >/dev/null 2>&1 || true
INNER_EOF

echo "==> Uploading swarm.sh into orchestrator..."
if command -v cygpath >/dev/null 2>&1; then
    SWARM_SH_HOST="$(cygpath -w "$SWARM_SH")"
else
    SWARM_SH_HOST="$SWARM_SH"
fi
aca sandbox fs write --id "$ORCH_ID" --path /tmp/swarm.sh --file "$SWARM_SH_HOST"
rm -f "$SWARM_SH"

# ---------------- 5. Run swarm inside orchestrator ----------------
echo "==> Orchestrator: spawning $WORKERS workers + aggregator in $WORKER_GROUP via MI..."
ENV_LINE="ACA_SUBSCRIPTION=$ACA_SUBSCRIPTION ACA_RESOURCE_GROUP=$ACA_RESOURCE_GROUP \
ACA_SANDBOX_GROUP=$WORKER_GROUP ACA_SANDBOX_MANAGED_IDENTITY=system \
ACA_REGION=$ACA_SANDBOXGROUP_REGION WORKERS=$WORKERS DARTS=$DARTS_PER_WORKER \
VOLUME_NAME=$VOLUME_NAME RUN_ID=$RUN_ID"

SWARM_OUTPUT="$(aca sandbox exec --id "$ORCH_ID" -c "$ENV_LINE bash /tmp/swarm.sh")"
echo "$SWARM_OUTPUT"

# ---------------- 6. Aggregate Pi on the host ----------------
echo "==> Aggregating across $((WORKERS * DARTS_PER_WORKER)) darts (from aggregator RESULT line)..."
RESULT_LINE="$(printf '%s\n' "$SWARM_OUTPUT" | grep -E '^RESULT INSIDE=' | tail -n1)"
TOTAL_INSIDE="$(printf '%s\n' "$RESULT_LINE" | grep -oE 'INSIDE=[0-9]+' | cut -d= -f2)"
TOTAL_DARTS="$( printf '%s\n' "$RESULT_LINE" | grep -oE 'TOTAL=[0-9]+'  | cut -d= -f2)"

if [[ -z "${TOTAL_DARTS:-}" || "$TOTAL_DARTS" -eq 0 ]]; then
    echo "error: aggregator did not report a RESULT line — see output above" >&2
    exit 1
fi
PI=$(awk "BEGIN{pi=4*$TOTAL_INSIDE/$TOTAL_DARTS; err=pi-3.141592653589793; if(err<0)err=-err; printf \"pi ≈ %.6f  (error %.2e)\", pi, err}")
echo "    $PI"

echo "==> Done."
