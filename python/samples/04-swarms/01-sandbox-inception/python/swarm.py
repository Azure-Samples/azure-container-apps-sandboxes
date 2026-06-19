"""Sandbox inception swarm, orchestrator sandbox spawns N workers in another group.

The host script provisions two fresh per-run sandbox groups (orchestrator
with SystemAssigned managed identity, plus a worker group with `Data
Owner` granted to the orchestrator's MI), boots an orchestrator
sandbox in the orchestrator group, then asks it to fan out N=4 Monte
Carlo Pi workers in the worker group **using only managed identity**,
no credential is ever materialised inside the agent code.

The full scenario story (architecture diagram + production tips) lives
in `../README.md`.

Reads configuration from `samples/.env` (written by
`python/samples/setup/setup.py` or `setup/cli/setup.sh`).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.containerapps.sandbox import (
    SandboxGroupClient,
    SandboxGroupManagementClient,
    endpoint_for_region,
)

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROLE_NAME = "Container Apps SandboxGroup Data Owner"
SDK_PACKAGE = "azure-containerapps-sandbox==0.1.0b1"
ORCH_DISK = "python-3.14"
WORKERS = 4
DARTS_PER_WORKER = 1_000_000
RBAC_PROPAGATION_SECONDS = 20


# ---------------------------------------------------------------------------
# This script runs INSIDE the orchestrator sandbox. It uses ManagedIdentity
# (the sandbox group's MI) to talk to the worker sandbox group, spawns N
# workers concurrently, runs a Monte Carlo Pi sample on each, and emits a
# single `RESULT={...json...}` line that the host script parses.
# ---------------------------------------------------------------------------
SPAWN_WORKERS_SCRIPT = textwrap.dedent("""\
    from __future__ import annotations

    import asyncio
    import json
    import os
    import time

    from azure.identity.aio import ManagedIdentityCredential
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.containerapps.sandbox import endpoint_for_region

    SUBSCRIPTION = os.environ["AZURE_SUBSCRIPTION_ID"]
    RG           = os.environ["ACA_RESOURCE_GROUP"]
    WORKER_GROUP = os.environ["WORKER_SANDBOX_GROUP"]
    REGION       = os.environ["ACA_SANDBOXGROUP_REGION"]
    WORKERS      = int(os.environ["WORKERS"])
    DARTS        = int(os.environ["DARTS_PER_WORKER"])

    PI_SNIPPET = (
        "python3 -c \\"import random as r, sys; "
        "n=int(sys.argv[1]); "
        "i=sum(1 for _ in range(n) if r.random()**2 + r.random()**2 < 1.0); "
        "print(f'INSIDE={i} TOTAL={n}')\\" " + str(DARTS)
    )


    async def run_worker(client, i):
        t0 = time.perf_counter()
        poller = await client.begin_create_sandbox(
            disk=\"ubuntu\",
            labels={\"swarm\": \"sandbox-inception\", \"worker\": str(i)},
        )
        sandbox = await poller.result()
        exec_result = await sandbox.exec(PI_SNIPPET)
        elapsed = time.perf_counter() - t0
        inside = total = 0
        for tok in (exec_result.stdout or \"\").split():
            if tok.startswith(\"INSIDE=\"):
                inside = int(tok.split(\"=\", 1)[1])
            elif tok.startswith(\"TOTAL=\"):
                total = int(tok.split(\"=\", 1)[1])
        return sandbox, {
            \"worker\": i,
            \"sandbox_id\": sandbox.sandbox_id,
            \"inside\": inside,
            \"total\": total,
            \"elapsed_s\": round(elapsed, 2),
        }


    async def main():
        cred = ManagedIdentityCredential()
        client = SandboxGroupClient(
            endpoint_for_region(REGION),
            cred,
            subscription_id=SUBSCRIPTION,
            resource_group=RG,
            sandbox_group=WORKER_GROUP,
        )
        sandboxes = []
        try:
            results = await asyncio.gather(*(run_worker(client, i) for i in range(WORKERS)))
            for sandbox, _ in results:
                sandboxes.append(sandbox)
            payload = [r for _, r in results]
            print(\"RESULT=\" + json.dumps(payload))
        finally:
            if sandboxes:
                await asyncio.gather(
                    *(s.delete() for s in sandboxes),
                    return_exceptions=True,
                )
            await client.close()
            await cred.close()


    if __name__ == \"__main__\":
        asyncio.run(main())
""")


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it."""
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break
    if not os.environ.get("ACA_SANDBOXGROUP_REGION"):
        sys.exit(
            "error: samples/.env is missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


def _assign_role(
    auth: AuthorizationManagementClient,
    scope: str,
    principal_id: str,
    principal_type: str = "ServicePrincipal",
) -> None:
    role_def = next(
        auth.role_definitions.list(scope, filter=f"roleName eq '{ROLE_NAME}'"),
        None,
    )
    if role_def is None:
        sys.exit(f"error: role '{ROLE_NAME}' not found at scope {scope}")
    try:
        auth.role_assignments.create(
            scope,
            str(uuid.uuid4()),
            {
                "role_definition_id": role_def.id,
                "principal_id": principal_id,
                "principal_type": principal_type,
            },
        )
        print(f"    granted to principal {principal_id}")
    except HttpResponseError as exc:
        if "RoleAssignmentExists" in str(exc) or "Conflict" in str(exc):
            print("    role already assigned (skipping)")
        else:
            raise


def _resolve_host_principal() -> tuple[str, str]:
    """Principal that boots the orchestrator sandbox (the host running this).

    Interactive default: the signed-in az CLI user. For automation without
    a signed-in user, set ACA_PRINCIPAL_ID, and optionally ACA_PRINCIPAL_TYPE
    (defaults to ServicePrincipal).
    """
    override = os.environ.get("ACA_PRINCIPAL_ID")
    if override:
        return override, os.environ.get("ACA_PRINCIPAL_TYPE", "ServicePrincipal")
    result = subprocess.run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"error: could not resolve signed-in user: {result.stderr.strip()}")
    return result.stdout.strip(), "User"


def main() -> None:
    _load_env()

    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["ACA_RESOURCE_GROUP"]
    region = os.environ["ACA_SANDBOXGROUP_REGION"]

    suffix = uuid.uuid4().hex[:6]
    orch_group = f"swarm-orch-{suffix}"
    worker_group = f"swarm-workers-{suffix}"

    cred = DefaultAzureCredential()
    mgmt = SandboxGroupManagementClient(
        cred, subscription_id=subscription, resource_group=resource_group,
    )
    auth = AuthorizationManagementClient(cred, subscription)

    orch_client: SandboxGroupClient | None = None
    orchestrator = None
    try:
        # ---- 1. Orchestrator group with SystemAssigned MI ------------------
        print(f"==> Provisioning orchestrator group {orch_group!r} with SystemAssigned MI...")
        mgmt.begin_create_group(
            orch_group, region, identity={"type": "SystemAssigned"},
        ).result()
        # principalId may take a moment to appear; re-read if missing.
        principal_id = (mgmt.get_group(orch_group).identity or {}).get("principalId")
        for _ in range(10):
            if principal_id:
                break
            time.sleep(2)
            principal_id = (mgmt.get_group(orch_group).identity or {}).get("principalId")
        if not principal_id:
            sys.exit("error: orchestrator group has no principalId, MI not enabled?")
        print(f"    principalId: {principal_id}")

        # ---- 2. Worker group ----------------------------------------------
        print(f"==> Provisioning worker group {worker_group!r}...")
        mgmt.begin_create_group(worker_group, region).result()

        # ---- 3. Grant Data Owner: MI on worker group, host on orchestrator group
        print(f"==> Granting {ROLE_NAME!r} on worker group → orchestrator MI...")
        worker_scope = (
            f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/sandboxGroups/{worker_group}"
        )
        _assign_role(auth, worker_scope, principal_id)

        # The host created the orchestrator group fresh, so it has no
        # data-plane grant there yet (setup only grants on the samples
        # group). Grant the host Data Owner on the orchestrator group so it
        # can boot the orchestrator sandbox below.
        host_id, host_type = _resolve_host_principal()
        print(f"==> Granting {ROLE_NAME!r} on orchestrator group → host {host_type.lower()}...")
        orch_scope = (
            f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/sandboxGroups/{orch_group}"
        )
        _assign_role(auth, orch_scope, host_id, host_type)

        print(f"==> Waiting {RBAC_PROPAGATION_SECONDS}s for RBAC propagation...")
        time.sleep(RBAC_PROPAGATION_SECONDS)

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
            labels={"swarm": "sandbox-inception", "role": "orchestrator"},
        ).result()
        print(f"    orchestrator: {orchestrator.sandbox_id}")

        # ---- 5. Bootstrap orchestrator ------------------------------------
        print("==> Uploading spawn_workers.py into orchestrator...")
        orchestrator.write_file("/tmp/spawn_workers.py", SPAWN_WORKERS_SCRIPT.encode())
        print(f"==> Installing SDK ({SDK_PACKAGE}) inside orchestrator from PyPI...")
        install = orchestrator.exec(
            "pip install --quiet --break-system-packages "
            f"{SDK_PACKAGE} azure-identity"
        )
        if install.exit_code != 0:
            sys.exit(f"orchestrator pip install failed:\n{install.stderr}")

        # ---- 6. Run the swarm ---------------------------------------------
        print(f"==> Orchestrator: spawning {WORKERS} workers in {worker_group!r} via MI...")
        env_prefix = (
            f"AZURE_SUBSCRIPTION_ID={subscription} "
            f"ACA_RESOURCE_GROUP={resource_group} "
            f"WORKER_SANDBOX_GROUP={worker_group} "
            f"ACA_SANDBOXGROUP_REGION={region} "
            f"WORKERS={WORKERS} "
            f"DARTS_PER_WORKER={DARTS_PER_WORKER}"
        )
        run = orchestrator.exec(f"{env_prefix} python3 /tmp/spawn_workers.py")
        if run.exit_code != 0:
            sys.exit(
                f"spawn_workers.py failed (exit={run.exit_code}):\n"
                f"stdout: {run.stdout}\nstderr: {run.stderr}"
            )

        # ---- 7. Aggregate Pi ----------------------------------------------
        payload = None
        for line in (run.stdout or "").splitlines():
            if line.startswith("RESULT="):
                payload = json.loads(line[len("RESULT="):])
                break
        if payload is None:
            sys.exit(f"could not find RESULT= line in:\n{run.stdout}")

        for r in payload:
            print(
                f"    worker {r['worker']}: {r['elapsed_s']}s, "
                f"{r['inside']:,} / {r['total']:,} inside"
            )
        total_inside = sum(r["inside"] for r in payload)
        total_darts = sum(r["total"] for r in payload)
        pi_est = 4.0 * total_inside / total_darts
        from math import pi
        err = abs(pi_est - pi)
        print(f"==> Aggregating across {total_darts:,} darts...")
        print(f"    π ≈ {pi_est:.6f}  (error {err:.2e})")
    finally:
        print("==> Cleaning up workers, orchestrator, both groups...")
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
            except Exception as exc:
                print(f"    cleanup warning ({grp}): {exc}")
        mgmt.close()
        auth.close()
        cred.close()
        print("==> Done.")


if __name__ == "__main__":
    main()
