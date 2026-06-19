"""Autonomous Swarm launcher — provisions per-run sandbox groups, grants
Managed Identity the AOAI + ACA roles it needs, then exec's a supervisor
INSIDE the orchestrator sandbox.

Zero AOAI keys are passed into any sandbox; the supervisor uses the
orchestrator's SystemAssigned MI for both Azure OpenAI and the worker
group's data plane.

Reads configuration from samples/.env (walked up from this script).
Required env keys:
    AZURE_SUBSCRIPTION_ID, ACA_RESOURCE_GROUP, ACA_SANDBOXGROUP_REGION,
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
Optional:
    AZURE_OPENAI_RESOURCE_ID  (auto-derived from endpoint if missing)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional, Tuple

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest
from azure.containerapps.sandbox import (
    SandboxGroupClient,
    SandboxGroupManagementClient,
    endpoint_for_region,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLE_SANDBOX_DATA_OWNER = "Container Apps SandboxGroup Data Owner"
ROLE_AOAI_USER = "Cognitive Services OpenAI User"

ACA_SDK_PACKAGE = "azure-containerapps-sandbox==0.1.0b1"

ORCH_DISK = "python-3.14"

# Brief host-side wait after RBAC grants. Supervisor does the real polling.
RBAC_HOST_WAIT_SECONDS = 60

# Where files get staged inside the sandbox
REMOTE_TMP = "/tmp"
REMOTE_EXT_DIR = f"{REMOTE_TMP}/agents-extension"
REMOTE_SUPERVISOR = f"{REMOTE_TMP}/supervisor.py"
REMOTE_CONFIG = f"{REMOTE_TMP}/config.json"
REMOTE_SUP_LOG = f"{REMOTE_TMP}/supervisor.log"
REMOTE_SUP_DONE = f"{REMOTE_TMP}/supervisor.done"

# Poll cadence + max wall-clock for the supervisor (covers full mode).
SUPERVISOR_POLL_SECONDS = 8
SUPERVISOR_MAX_WAIT_SECONDS = 1500  # 25 min

THIS_DIR = Path(__file__).resolve().parent
EXTENSION_DIR = THIS_DIR.parent / "sandbox-agent-extension"
SUPERVISOR_SRC = THIS_DIR / "supervisor.py"


# ---------------------------------------------------------------------------
# Env loading + AOAI resource id resolution
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it (setdefault)."""
    for parent in Path(__file__).resolve().parents:
        env = parent / "samples" / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
        # Stop at filesystem root
        if parent.parent == parent:
            return


REQUIRED_ENV = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
)

# Accept either naming convention for ACA control-plane settings.
ENV_ALIASES = {
    "AZURE_SUBSCRIPTION_ID": ("AZURE_SUBSCRIPTION_ID", "ACA_SUBSCRIPTION"),
    "ACA_RESOURCE_GROUP": ("ACA_RESOURCE_GROUP",),
    "ACA_SANDBOXGROUP_REGION": ("ACA_SANDBOXGROUP_REGION", "ACA_REGION"),
}


def _check_required_env() -> dict:
    out = {}
    missing = []
    for key in REQUIRED_ENV:
        v = os.environ.get(key)
        if not v:
            missing.append(key)
        else:
            out[key] = v.strip()
    for canonical, aliases in ENV_ALIASES.items():
        v = None
        for a in aliases:
            v = os.environ.get(a)
            if v:
                break
        if not v:
            missing.append(f"{canonical} (or one of: {', '.join(aliases)})")
        else:
            out[canonical] = v.strip()
    if missing:
        sys.exit(
            "error: samples/.env is missing required keys: "
            + ", ".join(missing)
            + ".\nSee 03-autonomous-swarm/.env.example for the schema."
        )
    return out


def _resolve_aoai_resource_id(
    cred: DefaultAzureCredential, endpoint: str
) -> str:
    """Return the AOAI account's ARM resource id.

    Prefer AZURE_OPENAI_RESOURCE_ID from env. Otherwise query Azure
    Resource Graph by the endpoint hostname's account name.
    """
    explicit = os.environ.get("AZURE_OPENAI_RESOURCE_ID", "").strip()
    if explicit:
        return explicit

    host = urllib.parse.urlparse(endpoint).hostname or ""
    account = host.split(".", 1)[0] if host else ""
    if not account:
        sys.exit(
            "error: could not parse account from AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_RESOURCE_ID is not set. Add AZURE_OPENAI_RESOURCE_ID "
            "to samples/.env."
        )

    print(f"==> Resolving AZURE_OPENAI_RESOURCE_ID for account {account!r} via Resource Graph...")
    rg = ResourceGraphClient(cred)
    query = (
        "Resources | where type =~ 'microsoft.cognitiveservices/accounts' "
        f"| where name =~ '{account}' | project id"
    )
    try:
        resp = rg.resources(QueryRequest(query=query))
    except HttpResponseError as exc:
        sys.exit(
            f"error: Resource Graph query failed: {exc.message}\n"
            "Set AZURE_OPENAI_RESOURCE_ID explicitly in samples/.env."
        )
    data = resp.data or []
    if not data:
        sys.exit(
            f"error: could not find AOAI account named {account!r} in any sub you can "
            "read. Set AZURE_OPENAI_RESOURCE_ID explicitly in samples/.env."
        )
    rid = data[0].get("id") if isinstance(data[0], dict) else None
    if not rid:
        sys.exit(f"error: Resource Graph returned unexpected shape: {data[0]!r}")
    print(f"    {rid}")
    return rid


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------


def _role_def_id(
    auth: AuthorizationManagementClient, scope: str, role_name: str
) -> str:
    role = next(
        auth.role_definitions.list(scope, filter=f"roleName eq '{role_name}'"),
        None,
    )
    if role is None:
        sys.exit(f"error: role {role_name!r} not found at scope {scope}")
    return role.id


def _assign_role(
    auth: AuthorizationManagementClient,
    scope: str,
    role_name: str,
    principal_id: str,
) -> Optional[Tuple[str, str]]:
    """Grant a role to a principal at a scope. Returns (scope, assignment_id)
    for cleanup, or None if the assignment already exists (no cleanup needed —
    not ours to delete).
    """
    role_def_id = _role_def_id(auth, scope, role_name)
    assignment_id = str(uuid.uuid4())
    try:
        auth.role_assignments.create(
            scope,
            assignment_id,
            {
                "role_definition_id": role_def_id,
                "principal_id": principal_id,
                "principal_type": "ServicePrincipal",
            },
        )
        print(f"    granted {role_name!r} to {principal_id}")
        return (scope, assignment_id)
    except HttpResponseError as exc:
        msg = str(exc)
        if "RoleAssignmentExists" in msg or "Conflict" in msg:
            print(f"    {role_name!r} already assigned (skipping cleanup)")
            return None
        raise


def _delete_role_assignment(
    auth: AuthorizationManagementClient, scope: str, assignment_id: str
) -> None:
    try:
        auth.role_assignments.delete(scope, assignment_id)
    except Exception as exc:  # best-effort cleanup
        print(f"    cleanup warning (role assignment {assignment_id}): {exc}")


# ---------------------------------------------------------------------------
# Sandbox bootstrap helpers
# ---------------------------------------------------------------------------


def _build_extension_tarball() -> bytes:
    """Package the agents_aca_sandboxes extension source tree (no caches)."""
    if not EXTENSION_DIR.is_dir():
        sys.exit(f"error: extension dir not found: {EXTENSION_DIR}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def filt(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
            skip = ("__pycache__", ".pytest_cache", ".egg-info", "dist", "build")
            if any(part in ti.name for part in skip):
                return None
            return ti
        tar.add(str(EXTENSION_DIR), arcname="agents-extension", filter=filt)
    return buf.getvalue()


BOOTSTRAP_SCRIPT_TEMPLATE = """
set -eu
echo "==> bootstrap: upgrade certifi/pip/wheel/hatchling"
pip install --quiet --break-system-packages --upgrade certifi pip wheel hatchling
echo "==> bootstrap: untar extension"
mkdir -p {ext_dir}
tar xzf {ext_dir}.tar.gz -C {tmp}
echo "==> bootstrap: install ACA SDK + openai-agents + azure-identity"
pip install --quiet --break-system-packages "{sdk_pkg}"
pip install --quiet --break-system-packages "openai-agents>=0.17.0,<0.20.0" "azure-identity>=1.16.0"
echo "==> bootstrap: install agents_aca_sandboxes from source"
pip install --quiet --break-system-packages --no-build-isolation --no-deps {ext_dir}
echo "==> bootstrap: done"
""".strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Autonomous Swarm — runs harness IN compute, zero AOAI keys.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--task",
        default=(
            "Compare the SandboxGroupClient async vs sync API surface of "
            "azure-containerapps-sandbox. For each of the worker subtasks, "
            "investigate the relevant source files and summarize differences."
        ),
        help="Research task for the swarm.",
    )
    p.add_argument(
        "--workers", type=int, default=3, help="Number of parallel SandboxAgent workers."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Provision + upload + import-check. Skip all AOAI/worker work.",
    )
    mode.add_argument(
        "--smoke-run",
        action="store_true",
        help="Provision + auth check (MI token + AOAI /models + worker group list).",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Don't tear down sandboxes/groups (for debugging).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _load_env()
    env = _check_required_env()

    if not SUPERVISOR_SRC.is_file():
        sys.exit(f"error: supervisor source not found: {SUPERVISOR_SRC}")

    subscription = env["AZURE_SUBSCRIPTION_ID"]
    resource_group = env["ACA_RESOURCE_GROUP"]
    region = env["ACA_SANDBOXGROUP_REGION"]
    aoai_endpoint = env["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    aoai_deployment = env["AZURE_OPENAI_DEPLOYMENT"]
    aoai_api_version = env["AZURE_OPENAI_API_VERSION"]

    run_id = uuid.uuid4().hex[:6]
    orch_group = f"swarm-aas-orch-{run_id}"
    worker_group = f"swarm-aas-wkrs-{run_id}"

    cred = DefaultAzureCredential()
    mgmt = SandboxGroupManagementClient(
        cred, subscription_id=subscription, resource_group=resource_group,
    )
    auth = AuthorizationManagementClient(cred, subscription)

    aoai_resource_id = _resolve_aoai_resource_id(cred, aoai_endpoint)

    orch_client: Optional[SandboxGroupClient] = None
    orchestrator = None
    worker_scope = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.App/sandboxGroups/{worker_group}"
    )
    role_assignments_to_cleanup: list[Tuple[str, str]] = []

    try:
        # ---- 1. Orchestrator group with SystemAssigned MI ------------------
        print(f"==> Provisioning orchestrator group {orch_group!r} with SystemAssigned MI...")
        mgmt.begin_create_group(
            orch_group, region, identity={"type": "SystemAssigned"},
        ).result()
        principal_id = (mgmt.get_group(orch_group).identity or {}).get("principalId")
        for _ in range(10):
            if principal_id:
                break
            time.sleep(2)
            principal_id = (mgmt.get_group(orch_group).identity or {}).get("principalId")
        if not principal_id:
            sys.exit("error: orchestrator group has no principalId — MI not enabled?")
        print(f"    principalId: {principal_id}")

        # ---- 2. Worker group ----------------------------------------------
        print(f"==> Provisioning worker group {worker_group!r}...")
        mgmt.begin_create_group(worker_group, region).result()

        # ---- 3. Grant RBAC: workers (Data Owner) + AOAI (OpenAI User) -----
        print(f"==> Granting {ROLE_SANDBOX_DATA_OWNER!r} on worker group -> orchestrator MI...")
        a = _assign_role(auth, worker_scope, ROLE_SANDBOX_DATA_OWNER, principal_id)
        if a:
            role_assignments_to_cleanup.append(a)

        print(f"==> Granting {ROLE_AOAI_USER!r} on AOAI account -> orchestrator MI...")
        a = _assign_role(auth, aoai_resource_id, ROLE_AOAI_USER, principal_id)
        if a:
            role_assignments_to_cleanup.append(a)

        print(f"==> Waiting {RBAC_HOST_WAIT_SECONDS}s for initial RBAC propagation "
              f"(supervisor will retry up to 5 min if needed)...")
        time.sleep(RBAC_HOST_WAIT_SECONDS)

        # ---- 4. Orchestrator sandbox --------------------------------------
        print(f"==> Creating orchestrator sandbox (disk={ORCH_DISK!r}) in {orch_group!r}...")
        orch_client = SandboxGroupClient(
            endpoint_for_region(region), cred,
            subscription_id=subscription,
            resource_group=resource_group,
            sandbox_group=orch_group,
        )
        orchestrator = orch_client.begin_create_sandbox(
            disk=ORCH_DISK,
            labels={
                "scenario": "08-sandbox-agents",
                "demo": "03-autonomous-swarm",
                "role": "orchestrator",
                "run-id": run_id,
            },
        ).result()
        print(f"    orchestrator: {orchestrator.sandbox_id}")

        # ---- 5. Upload artifacts ------------------------------------------
        print("==> Uploading supervisor.py + config.json + extension tarball...")
        ext_tar = _build_extension_tarball()
        supervisor_bytes = SUPERVISOR_SRC.read_bytes()

        config = {
            "run_id": run_id,
            "mode": "dry-run" if args.dry_run else ("smoke-run" if args.smoke_run else "full"),
            "task": args.task,
            "workers": args.workers,
            "aoai": {
                "endpoint": aoai_endpoint,
                "deployment": aoai_deployment,
                "api_version": aoai_api_version,
            },
            "worker_group": {
                "subscription_id": subscription,
                "resource_group": resource_group,
                "region": region,
                "sandbox_group": worker_group,
            },
        }

        orchestrator.write_file(f"{REMOTE_EXT_DIR}.tar.gz", ext_tar)
        orchestrator.write_file(REMOTE_SUPERVISOR, supervisor_bytes)
        orchestrator.write_file(
            REMOTE_CONFIG, json.dumps(config, indent=2).encode("utf-8")
        )

        # ---- 6. Bootstrap dependencies in orchestrator --------------------
        print("==> Bootstrapping dependencies inside orchestrator...")
        bootstrap = BOOTSTRAP_SCRIPT_TEMPLATE.format(
            tmp=REMOTE_TMP,
            ext_dir=REMOTE_EXT_DIR,
            sdk_pkg=ACA_SDK_PACKAGE,
        )
        orchestrator.write_file(f"{REMOTE_TMP}/bootstrap.sh", bootstrap.encode("utf-8"))
        install = orchestrator.exec(f"bash {REMOTE_TMP}/bootstrap.sh")
        if install.exit_code != 0:
            print(install.stdout, file=sys.stderr)
            sys.exit(f"bootstrap failed (exit={install.exit_code}):\n{install.stderr}")

        # ---- 7. Run supervisor (mode-dependent) ---------------------------
        if args.dry_run:
            print("==> --dry-run: verifying imports inside sandbox...")
            check = orchestrator.exec(
                "python3 -c 'import agents, agents_aca_sandboxes, "
                "azure.identity, azure.containerapps.sandbox; print(\"imports ok\")'"
            )
            if check.exit_code != 0:
                sys.exit(f"import check failed:\n{check.stderr}")
            print(f"    {check.stdout.strip()}")
            return 0

        print(f"==> Executing supervisor (mode={config['mode']!r}) in background...")
        # Detach supervisor as background process so the host-side exec returns
        # quickly. We then poll for /tmp/supervisor.done. This sidesteps the
        # ACA SDK's fixed 300s read timeout on the data-plane exec endpoint.
        bg = orchestrator.exec(
            "bash -lc '"
            f"rm -f {REMOTE_SUP_DONE} {REMOTE_SUP_LOG}; "
            f"setsid nohup python3 {REMOTE_SUPERVISOR} {REMOTE_CONFIG} "
            f"> {REMOTE_SUP_LOG} 2>&1 < /dev/null & "
            f"disown; echo pid=$!"
            "'"
        )
        if bg.exit_code != 0:
            sys.exit(f"failed to background supervisor: {bg.stderr}")
        print(f"    {bg.stdout.strip()}")

        # Poll for completion.
        t_start = time.time()
        last_log_size = 0
        while True:
            elapsed = int(time.time() - t_start)
            if elapsed > SUPERVISOR_MAX_WAIT_SECONDS:
                print(f"==> Supervisor exceeded {SUPERVISOR_MAX_WAIT_SECONDS}s; aborting.")
                tail = orchestrator.exec(f"tail -n 80 {REMOTE_SUP_LOG} 2>/dev/null || echo no log")
                print(tail.stdout)
                sys.exit(2)
            # Check completion + stream new log bytes.
            check = orchestrator.exec(
                f"test -f {REMOTE_SUP_DONE} && echo DONE || echo RUN; "
                f"stat -c%s {REMOTE_SUP_LOG} 2>/dev/null || echo 0"
            )
            lines = check.stdout.strip().splitlines()
            status = lines[0] if lines else "RUN"
            try:
                log_size = int(lines[1]) if len(lines) > 1 else 0
            except ValueError:
                log_size = 0
            if log_size > last_log_size:
                delta = orchestrator.exec(
                    f"tail -c +{last_log_size + 1} {REMOTE_SUP_LOG} 2>/dev/null"
                )
                if delta.stdout:
                    for line in delta.stdout.rstrip("\n").splitlines():
                        print(f"    [sup] {line}")
                last_log_size = log_size
            if status == "DONE":
                break
            time.sleep(SUPERVISOR_POLL_SECONDS)

        # Read final log + exit code marker.
        final = orchestrator.exec(
            f"cat {REMOTE_SUP_DONE}; echo ---; cat {REMOTE_SUP_LOG}"
        )
        # First chunk is supervisor exit code, second is full log.
        parts = final.stdout.split("---\n", 1)
        try:
            sup_exit_code = int(parts[0].strip().splitlines()[0])
        except (ValueError, IndexError):
            sup_exit_code = 1
        sup_log = parts[1] if len(parts) > 1 else ""
        if sup_exit_code != 0:
            print("---supervisor log tail---", file=sys.stderr)
            print("\n".join(sup_log.splitlines()[-40:]), file=sys.stderr)
            sys.exit(f"supervisor failed (exit={sup_exit_code})")

        # ---- 8. Aggregate RESULT line -------------------------------------
        payload = None
        for line in sup_log.splitlines():
            if line.startswith("RESULT="):
                payload = json.loads(line[len("RESULT="):])
                break
        if payload is None:
            sys.exit("error: no RESULT= line in supervisor log")

        print()
        print("=" * 60)
        print(f"FINAL ANSWER (run-id {run_id}, mode={config['mode']}):")
        print("=" * 60)
        if config["mode"] == "smoke-run":
            print(json.dumps(payload, indent=2))
        else:
            print(payload.get("final_answer", "<empty>"))
            print()
            print("Worker summary:")
            for w in payload.get("workers", []):
                status = "OK" if w.get("ok") else "FAIL"
                print(f"  [{status}] worker {w.get('worker_id')}: {w.get('summary', w.get('error', ''))[:140]}")
        return 0

    finally:
        print()
        print("==> Cleanup")
        if not args.keep:
            # Sweep workers labelled with our run-id (in case supervisor died mid-run).
            try:
                worker_client = SandboxGroupClient(
                    endpoint_for_region(region), cred,
                    subscription_id=subscription,
                    resource_group=resource_group,
                    sandbox_group=worker_group,
                )
                for sb in worker_client.list_sandboxes():
                    try:
                        sb.delete()
                        print(f"    deleted leftover worker {sb.sandbox_id}")
                    except Exception as exc:
                        print(f"    cleanup warning (worker {sb.sandbox_id}): {exc}")
                worker_client.close()
            except Exception as exc:
                print(f"    worker sweep warning: {exc}")

            if orchestrator is not None:
                try:
                    orchestrator.delete()
                except Exception as exc:
                    print(f"    cleanup warning (orchestrator): {exc}")
            if orch_client is not None:
                orch_client.close()

            for grp in (orch_group, worker_group):
                try:
                    mgmt.delete_group(grp)
                    print(f"    deleted group {grp}")
                except Exception as exc:
                    print(f"    cleanup warning ({grp}): {exc}")
        else:
            print(f"    --keep: leaving groups {orch_group!r} and {worker_group!r} in place")

        # ALWAYS clean up the AOAI role assignment, even with --keep.
        # The orchestrator MI lives with its group; if we're keeping the group,
        # we still must not leave that MI with AOAI access lying around. The
        # role assignment on the worker group is cleaned up by group deletion.
        for scope, assignment_id in role_assignments_to_cleanup:
            if "Microsoft.CognitiveServices" in scope:
                try:
                    _delete_role_assignment(auth, scope, assignment_id)
                    print(f"    deleted AOAI role assignment {assignment_id}")
                except Exception as exc:
                    print(f"    cleanup warning (AOAI role assignment): {exc}")

        mgmt.close()
        auth.close()
        cred.close()


if __name__ == "__main__":
    raise SystemExit(main())
