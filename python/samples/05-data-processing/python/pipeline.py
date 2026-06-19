"""Producer/consumer data pipeline across three sandboxes on a shared volume.

Architecture::

    producer sandbox    transformer sandbox    aggregator sandbox
            |                   |                       |
            |                   |                       |
            |   /mnt/shared (AzureBlob volume)          |
            |    ├── raw/                               |
    write --|--> raw/batch-NNN.jsonl                    |
            |       |                                   |
            |       +-- read, enrich, move ----+        |
            |                                  |        |
            |                                  v        |
            |       processed/batch-NNN.jsonl <--------+ |
            |                                          | |
            |                            read --------+| |
            |                                          | |
            |                            summary/report.json
            |                                            v
            |                          stdout: 'RESULT={...}' <---+
            |                                                     |
            +-----------------------------------------------------+
                                  host reads, prints, deletes

The producer and transformer run concurrently — the transformer
processes batches as they appear, while the producer keeps generating.
Once the producer drops the ``.producer-done`` sentinel and the
transformer's polling loop sees a quiet period, it exits. Then the
aggregator runs as a one-shot.

Every worker is plain stdlib ``open()`` / ``glob`` / ``json`` — no
Azure Blob SDK, no SAS tokens, no connection strings. The platform
brokers access to the shared storage via the mount.

Reads configuration from ``samples/.env`` (written by setup.py / setup.sh).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.containerapps.sandbox import SandboxVolume, endpoint_for_region
from azure.containerapps.sandbox.aio import SandboxGroupClient

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

THIS_DIR = Path(__file__).resolve().parent
WORKERS_DIR = THIS_DIR / "workers"

DISK = "python-3.14"
MOUNTPOINT = "/mnt/shared"
WORKER_DEST_DIR = "/tmp"


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it."""
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break
    if not os.environ.get("ACA_SANDBOXGROUP_REGION"):
        sys.exit(
            "error: samples/.env is missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


async def _stage_worker(sandbox, name: str, env_inline: str) -> None:
    """Upload `workers/{name}.py` to the sandbox under /tmp/."""
    src = WORKERS_DIR / f"{name}.py"
    dest = f"{WORKER_DEST_DIR}/{name}.py"
    await sandbox.write_file(dest, src.read_bytes())
    # Best-effort chmod so a direct `python3 /tmp/...` works.
    await sandbox.exec(f"chmod +x {dest}")
    print(f"    staged {name}.py into {sandbox.sandbox_id[:8]}…  ({src.stat().st_size:,} bytes)")


async def _run_worker(sandbox, name: str, env_pairs: dict[str, str]) -> tuple[int, str, str]:
    """Run a worker script with the given env and return (exit, stdout, stderr)."""
    env_inline = " ".join(f"{k}={v}" for k, v in env_pairs.items())
    cmd = f"{env_inline} python3 {WORKER_DEST_DIR}/{name}.py"
    print(f"    ▶ exec on {sandbox.sandbox_id[:8]}…: {name}.py")
    result = await sandbox.exec(cmd)
    return result.exit_code, result.stdout or "", result.stderr or ""


async def main_async() -> int:
    _load_env()

    region = os.environ["ACA_SANDBOXGROUP_REGION"]
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["ACA_RESOURCE_GROUP"]
    sandbox_group = os.environ["ACA_SANDBOX_GROUP"]
    run_id = uuid.uuid4().hex[:8]
    volume_name = f"pipeline-{run_id}"

    common_env = {
        "MOUNTPOINT": MOUNTPOINT,
        "BATCHES": os.environ.get("PIPELINE_BATCHES", "20"),
        "EVENTS_PER_BATCH": os.environ.get("PIPELINE_EVENTS_PER_BATCH", "100"),
        "BATCH_DELAY_S": os.environ.get("PIPELINE_BATCH_DELAY_S", "0.5"),
        "SEED": os.environ.get("PIPELINE_SEED", "42"),
    }

    print("=" * 72)
    print("PRODUCER/CONSUMER PIPELINE — shared AzureBlob volume")
    print("=" * 72)
    print(f"==> sandbox group : {sandbox_group} ({region})")
    print(f"==> run id        : {run_id}")
    print(f"==> volume        : {volume_name}")
    print(f"==> batches       : {common_env['BATCHES']} × {common_env['EVENTS_PER_BATCH']} events")
    print()

    cred = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint=endpoint_for_region(region),
        credential=cred,
        subscription_id=subscription,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    sandboxes: list[Any] = []
    volume_created = False
    try:
        # ---- 1. Create the shared volume ---------------------------------
        print(f"==> create_volume({volume_name!r}, type='AzureBlob')...")
        vol = await client.create_volume(volume_name, type="AzureBlob")
        volume_created = True
        print(f"    name={vol.name}  type={vol.type}")

        # ---- 2. Boot the producer + transformer concurrently --------------
        # Aggregator runs later (single-shot, after both streams are done).
        labels_common = {
            "scenario": "data-processing",
            "run": run_id,
        }
        volume_mount = [SandboxVolume(volume_name=volume_name, mountpoint=MOUNTPOINT)]

        print("==> Booting producer + transformer (concurrent)...")
        producer_poll, transformer_poll = await asyncio.gather(
            client.begin_create_sandbox(
                disk=DISK,
                labels={**labels_common, "role": "producer"},
                volumes=volume_mount,
            ),
            client.begin_create_sandbox(
                disk=DISK,
                labels={**labels_common, "role": "transformer"},
                volumes=volume_mount,
            ),
        )
        producer, transformer = await asyncio.gather(
            producer_poll.result(), transformer_poll.result(),
        )
        sandboxes.extend([producer, transformer])
        print(f"    producer:    {producer.sandbox_id}")
        print(f"    transformer: {transformer.sandbox_id}")

        # ---- 3. Stage worker scripts -------------------------------------
        await asyncio.gather(
            _stage_worker(producer, "producer", common_env),
            _stage_worker(transformer, "transformer", common_env),
        )

        # ---- 4. Run producer + transformer concurrently -------------------
        print("==> Running pipeline (producer streams, transformer drains)...")
        t0 = time.perf_counter()
        prod_result, xform_result = await asyncio.gather(
            _run_worker(producer, "producer", common_env),
            _run_worker(transformer, "transformer", common_env),
        )
        stream_elapsed = time.perf_counter() - t0

        prod_rc, prod_stdout, prod_stderr = prod_result
        xform_rc, xform_stdout, xform_stderr = xform_result
        print()
        print(f"--- producer stdout (exit={prod_rc}, {stream_elapsed:.1f}s wall) ---")
        for line in prod_stdout.splitlines()[-5:]:
            print(f"  {line}")
        if prod_rc != 0:
            print(f"--- producer stderr ---\n{prod_stderr}")
            return 1
        print(f"--- transformer stdout (exit={xform_rc}) ---")
        for line in xform_stdout.splitlines()[-5:]:
            print(f"  {line}")
        if xform_rc != 0:
            print(f"--- transformer stderr ---\n{xform_stderr}")
            return 1

        # ---- 5. Aggregator -----------------------------------------------
        print()
        print("==> Booting aggregator (reads /mnt/shared/processed/, writes summary)...")
        aggregator = await (await client.begin_create_sandbox(
            disk=DISK,
            labels={**labels_common, "role": "aggregator"},
            volumes=volume_mount,
        )).result()
        sandboxes.append(aggregator)
        print(f"    aggregator: {aggregator.sandbox_id}")

        await _stage_worker(aggregator, "aggregator", common_env)
        agg_rc, agg_stdout, agg_stderr = await _run_worker(
            aggregator, "aggregator", common_env,
        )
        if agg_rc != 0:
            print(f"aggregator failed (exit={agg_rc}):\n{agg_stderr}", file=sys.stderr)
            return 1

        # Find the RESULT= line.
        report = None
        for line in agg_stdout.splitlines():
            if line.startswith("RESULT="):
                report = json.loads(line[len("RESULT="):])
                break
        if report is None:
            print("error: aggregator did not emit a RESULT= line", file=sys.stderr)
            print(agg_stdout, file=sys.stderr)
            return 1

        print()
        print("=" * 72)
        print("PIPELINE REPORT")
        print("=" * 72)
        print(f"  files read         : {report['files_read']}")
        print(f"  total events       : {report['events_total']:,}")
        print(f"  revenue events     : {report['revenue_events']:,}")
        print(f"  total value        : {report['total_value']:,.2f}")
        print(f"  avg value / event  : {report['avg_value']:.4f}")
        print()
        print("  events by type:")
        for et, n in report["events_by_type"].items():
            print(f"    {et:14s} {n:6,}")
        print()
        print(f"  top {len(report['top_users'])} users by event count:")
        for u, n in report["top_users"]:
            print(f"    {u:8s} {n:5,}")
        print()
        print(f"  top {len(report['top_hours'])} hours (UTC) by event count:")
        for h, n in report["top_hours"]:
            print(f"    hour={h:02d}     {n:6,}")
        print()
        print("==> Done.")
        return 0
    finally:
        if sandboxes:
            print("==> Deleting sandboxes...")
            for s in sandboxes:
                try:
                    await s.delete()
                except Exception as exc:  # noqa: BLE001
                    print(f"    warning: delete {s.sandbox_id[:8]} failed: {exc}")
        if volume_created:
            print(f"==> delete_volume({volume_name!r})")
            try:
                await client.delete_volume(volume_name)
            except Exception as exc:  # noqa: BLE001
                print(f"    warning: delete_volume failed: {exc}")
        await client.close()
        await cred.close()


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
