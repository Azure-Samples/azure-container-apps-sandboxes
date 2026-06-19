"""Swarm with shared-blob memory — sandbox inception + a sandbox-group AzureBlob volume.

Same cross-group inception shape as
``../../01-sandbox-inception/python/swarm.py``: a host script provisions
two fresh sandbox groups (orchestrator with SystemAssigned MI, worker
group with ``Data Owner`` granted to the orchestrator's MI), boots an
orchestrator sandbox, then asks it to fan out N=4 Monte Carlo Pi
workers in the worker group **using only managed identity**.

The new piece: the worker group owns a single ``AzureBlob`` volume
called ``shared-memory``. Every worker mounts it at ``/mnt/shared``
and writes its per-checkpoint state to
``/mnt/shared/{run_id}/worker-{i}.json`` with a plain ``open()``.
After the workers exit (and their sandboxes are deleted), the
orchestrator spawns one final **aggregator sandbox** in the same
worker group, mounts the same volume read-only, lists the prefix,
reads every blob, and prints a single ``RESULT={json}`` line. This
demonstrates three things that are otherwise hard to show:

1. **Durable shared scratchpad** — the volume survives the workers
   that wrote to it.
2. **Cross-worker visibility without RPC** — siblings see each
   other's partial state by listing a prefix.
3. **Zero blob plumbing** — no storage account, no
   ``azure-storage-blob`` in the agent code, no RBAC on storage,
   no SAS / connection strings. Just ``open()``.

The full scenario story (architecture diagram + production tips)
lives in ``../README.md``.

Reads configuration from ``samples/.env`` (written by
``python/samples/setup/setup.py``).
"""

from __future__ import annotations

import json
import os
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
    SandboxVolume,
    endpoint_for_region,
)

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROLE_NAME = "Container Apps SandboxGroup Data Owner"
SDK_PACKAGE = "azure-containerapps-sandbox==0.1.0b1"
ORCH_DISK = "python-3.14"
WORKER_DISK = "python-3.14"
WORKERS = 4
DARTS_PER_WORKER = 1_000_000
CHECKPOINT_EVERY = 200_000
VOLUME_NAME = "shared-memory"
MOUNTPOINT = "/mnt/shared"
RBAC_PROPAGATION_SECONDS = 20


# ---------------------------------------------------------------------------
# Runs INSIDE the orchestrator sandbox. Uses ManagedIdentityCredential (the
# orchestrator group's MI) to (a) fan out N workers in the worker group, each
# mounted on the shared volume, and (b) launch a final aggregator sandbox
# that reads the workers' checkpoint files from the shared volume after the
# workers themselves are gone.
# ---------------------------------------------------------------------------
SPAWN_WORKERS_SCRIPT = textwrap.dedent('''\
    from __future__ import annotations

    import asyncio
    import json
    import os
    import textwrap

    from azure.identity.aio import ManagedIdentityCredential
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.containerapps.sandbox import SandboxVolume, endpoint_for_region

    SUBSCRIPTION = os.environ["AZURE_SUBSCRIPTION_ID"]
    RG           = os.environ["ACA_RESOURCE_GROUP"]
    WORKER_GROUP = os.environ["WORKER_SANDBOX_GROUP"]
    REGION       = os.environ["ACA_SANDBOXGROUP_REGION"]
    VOLUME       = os.environ["VOLUME_NAME"]
    MOUNT        = os.environ["MOUNTPOINT"]
    RUN_ID       = os.environ["RUN_ID"]
    WORKERS      = int(os.environ["WORKERS"])
    DARTS        = int(os.environ["DARTS_PER_WORKER"])
    CHECKPOINT   = int(os.environ["CHECKPOINT_EVERY"])

    # Per-worker script — runs inside the worker sandbox. Uses plain
    # `open()` against the mounted shared volume — no blob SDK, no auth.
    WORKER_PY = textwrap.dedent("""\\
        import json, os, random, sys

        i          = int(sys.argv[1])
        total      = int(sys.argv[2])
        checkpoint = int(sys.argv[3])
        mount      = os.environ["MOUNTPOINT"]
        run_id     = os.environ["RUN_ID"]

        run_dir = os.path.join(mount, run_id)
        os.makedirs(run_dir, exist_ok=True)
        path = os.path.join(run_dir, f"worker-{i}.json")

        inside = 0
        checkpoints = []
        for k in range(1, total + 1):
            x = random.random(); y = random.random()
            if x*x + y*y < 1.0:
                inside += 1
            if k % checkpoint == 0:
                checkpoints.append(k)
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump({
                        "worker": i,
                        "inside": inside,
                        "total":  k,
                        "checkpoints": checkpoints,
                        "done":   k == total,
                    }, f)
                os.replace(tmp, path)

        print(f"DONE worker={i} inside={inside} total={total} ckpts={len(checkpoints)}")
    """).strip()

    # Aggregator script — runs inside the final aggregator sandbox AFTER
    # the workers are deleted. Just lists the shared prefix and reads.
    AGGREGATOR_PY = textwrap.dedent("""\\
        import glob, json, os

        run_dir = os.path.join(os.environ["MOUNTPOINT"], os.environ["RUN_ID"])
        results = []
        for path in sorted(glob.glob(os.path.join(run_dir, "worker-*.json"))):
            with open(path) as f:
                results.append(json.load(f))
        results.sort(key=lambda r: r["worker"])
        print("AGGREGATED_FILES=" + str(len(results)))
        print("RESULT=" + json.dumps(results))
    """).strip()


    async def run_worker(client: SandboxGroupClient, i: int) -> str:
        poller = await client.begin_create_sandbox(
            disk=os.environ["WORKER_DISK"],
            labels={"swarm": "shared-blob-memory", "worker": str(i)},
            volumes=[SandboxVolume(volume_name=VOLUME, mountpoint=MOUNT)],
        )
        sandbox = await poller.result()
        try:
            await sandbox.write_file("/tmp/worker.py", WORKER_PY.encode())
            result = await sandbox.exec(
                f"MOUNTPOINT={MOUNT} RUN_ID={RUN_ID} "
                f"python3 /tmp/worker.py {i} {DARTS} {CHECKPOINT}"
            )
            if result.exit_code != 0:
                return f"FAIL worker={i} exit={result.exit_code} stderr={result.stderr[:400]}"
            return (result.stdout or "").strip().splitlines()[-1]
        finally:
            # Worker sandbox is gone — but its scratchpad blob stays in the
            # shared volume. That's the whole point of this variant.
            await sandbox.delete()


    async def run_aggregator(client: SandboxGroupClient) -> str:
        """Spawn a tiny sandbox in the worker group, mount the same volume,
        list the workers' checkpoint files, and emit RESULT=."""
        poller = await client.begin_create_sandbox(
            disk=os.environ["WORKER_DISK"],
            labels={"swarm": "shared-blob-memory", "role": "aggregator"},
            volumes=[SandboxVolume(volume_name=VOLUME, mountpoint=MOUNT)],
        )
        sandbox = await poller.result()
        try:
            await sandbox.write_file("/tmp/aggregate.py", AGGREGATOR_PY.encode())
            result = await sandbox.exec(
                f"MOUNTPOINT={MOUNT} RUN_ID={RUN_ID} python3 /tmp/aggregate.py"
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"aggregator exit={result.exit_code} stderr={result.stderr[:400]}"
                )
            return result.stdout or ""
        finally:
            await sandbox.delete()


    async def main():
        cred = ManagedIdentityCredential()
        sb_client = SandboxGroupClient(
            endpoint_for_region(REGION),
            cred,
            subscription_id=SUBSCRIPTION,
            resource_group=RG,
            sandbox_group=WORKER_GROUP,
        )
        try:
            # 1. Fan out N workers; each writes its checkpoints into the
            #    shared volume, then its sandbox is deleted.
            lines = await asyncio.gather(*(run_worker(sb_client, i) for i in range(WORKERS)))
            for line in lines:
                print(line)

            # 2. With every worker now gone, an aggregator sandbox reads
            #    the durable scratchpad they left behind.
            agg_out = await run_aggregator(sb_client)
            # Re-emit aggregator stdout so the host sees AGGREGATED_FILES= + RESULT=.
            for line in agg_out.splitlines():
                print(line)
        finally:
            await sb_client.close()
            await cred.close()


    if __name__ == "__main__":
        asyncio.run(main())
''')


def _load_env() -> None:
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
            "error: samples/.env missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


def _assign_role(auth: AuthorizationManagementClient, scope: str, principal_id: str) -> None:
    role_def = next(
        auth.role_definitions.list(scope, filter=f"roleName eq '{ROLE_NAME}'"),
        None,
    )
    if role_def is None:
        sys.exit(f"error: role '{ROLE_NAME}' not found at scope {scope}")
    # AAD replication for a brand-new MI principal can take 10-30s.
    last_exc: Exception | None = None
    for _ in range(10):
        try:
            auth.role_assignments.create(
                scope, str(uuid.uuid4()),
                {
                    "role_definition_id": role_def.id,
                    "principal_id": principal_id,
                    "principal_type": "ServicePrincipal",
                },
            )
            return
        except HttpResponseError as exc:
            msg = str(exc)
            if "RoleAssignmentExists" in msg or "Conflict" in msg:
                return
            if "PrincipalNotFound" in msg or "does not exist in the directory" in msg:
                last_exc = exc
                time.sleep(5)
                continue
            raise
    raise RuntimeError(f"role grant never succeeded: {last_exc}")


def _wait_for_principal(mgmt: SandboxGroupManagementClient, group: str) -> str:
    for _ in range(10):
        identity = mgmt.get_group(group).identity or {}
        pid = identity.get("principalId")
        if pid:
            return pid
        time.sleep(2)
    sys.exit(f"error: group {group!r} has no principalId — MI not enabled?")


def main() -> None:
    _load_env()
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["ACA_RESOURCE_GROUP"]
    region = os.environ["ACA_SANDBOXGROUP_REGION"]

    suffix = uuid.uuid4().hex[:8]
    orch_group = f"swarmblob-orch-{suffix}"
    worker_group = f"swarmblob-workers-{suffix}"
    run_id = uuid.uuid4().hex[:12]

    cred = DefaultAzureCredential()
    mgmt = SandboxGroupManagementClient(
        cred, subscription_id=subscription, resource_group=resource_group,
    )
    auth = AuthorizationManagementClient(cred, subscription)

    orch_client: SandboxGroupClient | None = None
    worker_client: SandboxGroupClient | None = None
    orchestrator = None
    volume_created = False

    try:
        # ---- 1. Two sandbox groups ----------------------------------------
        print(f"==> Provisioning orchestrator group {orch_group!r} (SystemAssigned MI)...")
        mgmt.begin_create_group(
            orch_group, region, identity={"type": "SystemAssigned"},
        ).result()
        orch_pid = _wait_for_principal(mgmt, orch_group)
        print(f"    orchestrator MI principalId: {orch_pid}")

        print(f"==> Provisioning worker group {worker_group!r}...")
        mgmt.begin_create_group(worker_group, region).result()

        # ---- 2. Grant Data Owner on worker group → orchestrator MI --------
        worker_group_scope = (
            f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/sandboxGroups/{worker_group}"
        )
        print(f"==> Granting {ROLE_NAME!r} on worker group → orchestrator MI...")
        _assign_role(auth, worker_group_scope, orch_pid)
        print(f"==> Waiting {RBAC_PROPAGATION_SECONDS}s for RBAC propagation...")
        time.sleep(RBAC_PROPAGATION_SECONDS)

        # ---- 3. Shared AzureBlob volume in the worker group ---------------
        print(f"==> Creating AzureBlob volume {VOLUME_NAME!r} in worker group...")
        worker_client = SandboxGroupClient(
            endpoint_for_region(region), cred,
            subscription_id=subscription,
            resource_group=resource_group,
            sandbox_group=worker_group,
        )
        vol = worker_client.create_volume(VOLUME_NAME, type="AzureBlob")
        volume_created = True
        print(f"    volume: name={vol.name} type={vol.type}")

        # ---- 4. Orchestrator sandbox + bootstrap --------------------------
        print(f"==> Creating orchestrator sandbox (disk={ORCH_DISK!r}) in {orch_group!r}...")
        orch_client = SandboxGroupClient(
            endpoint_for_region(region), cred,
            subscription_id=subscription,
            resource_group=resource_group,
            sandbox_group=orch_group,
        )
        orchestrator = orch_client.begin_create_sandbox(
            disk=ORCH_DISK,
            labels={"swarm": "shared-blob-memory", "role": "orchestrator"},
        ).result()
        print(f"    orchestrator: {orchestrator.sandbox_id}")

        print("==> Uploading spawn_workers.py into orchestrator...")
        orchestrator.write_file("/tmp/spawn_workers.py", SPAWN_WORKERS_SCRIPT.encode())

        print(f"==> Installing SDK ({SDK_PACKAGE}) inside orchestrator from PyPI...")
        install = orchestrator.exec(
            "pip install --quiet --break-system-packages "
            f"{SDK_PACKAGE} azure-identity aiohttp"
        )
        if install.exit_code != 0:
            sys.exit(f"orchestrator pip install failed:\n{install.stderr}")

        # ---- 5. Run the swarm ---------------------------------------------
        print(f"==> Orchestrator: spawning {WORKERS} workers in {worker_group!r} via MI...")
        env_prefix = (
            f"AZURE_SUBSCRIPTION_ID={subscription} "
            f"ACA_RESOURCE_GROUP={resource_group} "
            f"WORKER_SANDBOX_GROUP={worker_group} "
            f"ACA_SANDBOXGROUP_REGION={region} "
            f"WORKER_DISK={WORKER_DISK} "
            f"VOLUME_NAME={VOLUME_NAME} "
            f"MOUNTPOINT={MOUNTPOINT} "
            f"RUN_ID={run_id} "
            f"WORKERS={WORKERS} "
            f"DARTS_PER_WORKER={DARTS_PER_WORKER} "
            f"CHECKPOINT_EVERY={CHECKPOINT_EVERY}"
        )
        run = orchestrator.exec(f"{env_prefix} python3 /tmp/spawn_workers.py")
        if run.exit_code != 0:
            sys.exit(
                f"spawn_workers.py failed (exit={run.exit_code}):\n"
                f"stdout: {run.stdout}\nstderr: {run.stderr}"
            )

        # ---- 6. Parse worker DONE lines + aggregator RESULT ---------------
        payload = None
        aggregated_files = None
        for line in (run.stdout or "").splitlines():
            if line.startswith("DONE "):
                tokens = dict(t.split("=") for t in line.split()[1:] if "=" in t)
                print(
                    f"    worker {tokens.get('worker')}: "
                    f"{tokens.get('ckpts')} checkpoints, "
                    f"{int(tokens.get('inside', 0)):,} / "
                    f"{int(tokens.get('total', 0)):,} inside"
                )
            elif line.startswith("AGGREGATED_FILES="):
                aggregated_files = int(line.split("=", 1)[1])
            elif line.startswith("RESULT="):
                payload = json.loads(line[len("RESULT="):])
        if payload is None:
            sys.exit(f"no RESULT= line in orchestrator stdout:\n{run.stdout}")
        if aggregated_files != WORKERS:
            sys.exit(
                f"aggregator saw {aggregated_files} files but expected {WORKERS}\n"
                f"orchestrator stdout:\n{run.stdout}"
            )

        print(f"==> Aggregator sandbox read {aggregated_files} checkpoint files from "
              f"{MOUNTPOINT}/{run_id}/ AFTER all workers were deleted.")
        total_inside = sum(r["inside"] for r in payload)
        total_darts  = sum(r["total"]  for r in payload)
        pi_est = 4.0 * total_inside / total_darts
        from math import pi
        err = abs(pi_est - pi)
        print(f"==> Aggregating across {total_darts:,} darts...")
        print(f"    π ≈ {pi_est:.6f}  (error {err:.2e})")
    finally:
        print("==> Cleaning up orchestrator + volume + both groups...")
        if orchestrator is not None:
            try:
                orchestrator.delete()
            except Exception as exc:
                print(f"    warn (orchestrator delete): {exc}")
        if orch_client is not None:
            orch_client.close()
        if volume_created and worker_client is not None:
            try:
                worker_client.delete_volume(VOLUME_NAME)
            except Exception as exc:
                print(f"    warn (volume delete): {exc}")
        if worker_client is not None:
            worker_client.close()
        for grp in (orch_group, worker_group):
            try:
                mgmt.delete_group(grp)
            except Exception as exc:
                print(f"    warn (group {grp} delete): {exc}")
        mgmt.close()
        cred.close()
        print("==> Done.")


if __name__ == "__main__":
    main()
