"""Ephemeral CI runner — fresh sandbox per PR, snapshot-warm-started.

What this shows::

  1. Cold-boot a sandbox once, install pytest (~10–20 s).
  2. Take a snapshot of the "post-setup" state — this is the warm runner
     image. Tear the cold sandbox down.
  3. For each pending PR, in parallel, create a fresh sandbox **from
     that snapshot**, upload the PR's source + tests, run pytest, and
     report pass/fail to the host. Every PR runs in isolation — its own
     sandbox, its own filesystem, no leakage between PRs.
  4. Tear every PR sandbox down at the end. Delete the snapshot.

The result is the canonical "ephemeral CI runner" pattern: one
build per VM, but the warm-start trick keeps each PR's cold-start
cost at seconds, not tens of seconds.

Composes:

  guides/01-sandboxes    boot + exec
  guides/02-snapshots    create_snapshot + restore-from-snapshot
  guides/07-files        write_file for the PR diff

Run::

    cd python
    uv run ci.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential
from azure.containerapps.sandbox import endpoint_for_region
from azure.containerapps.sandbox.aio import SandboxGroupClient

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

THIS_DIR = Path(__file__).resolve().parent
PRS_DIR = THIS_DIR / "prs"

DISK = "python-3.14"
WORK_DIR = "/workspace"
SNAPSHOT_NAME = "ci-warm-runner"
DEFAULT_PRS = ("pr-1", "pr-2", "pr-3")
SETUP_CMD = "pip install --quiet --disable-pip-version-check --break-system-packages pytest"
SNAPSHOT_WARMUP_S = 12  # snapshot-restored sandboxes need a beat to settle


@dataclass
class PrResult:
    name: str
    sandbox_id: str
    boot_s: float = 0.0
    test_s: float = 0.0
    exit_code: int = -1
    summary: str = ""
    last_lines: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def _load_env() -> None:
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


async def _upload_pr(sandbox, pr_dir: Path) -> None:
    await sandbox.exec(f"mkdir -p {WORK_DIR}")
    for src in sorted(pr_dir.iterdir()):
        if src.is_file():
            await sandbox.write_file(f"{WORK_DIR}/{src.name}", src.read_bytes())


async def _run_pr(client, snap_id: str, pr_name: str, run_id: str) -> PrResult:
    pr_dir = PRS_DIR / pr_name
    if not pr_dir.is_dir():
        raise FileNotFoundError(f"missing PR directory: {pr_dir}")

    t_boot = time.perf_counter()
    # Snapshot restore replays the captured sandbox state as-is, so the SDK
    # rejects options like labels= here. The labels live on the *base*
    # sandbox + the snapshot itself instead.
    poller = await client.begin_create_sandbox(snapshot_id=snap_id)
    sandbox = await poller.result()
    # Snapshot-restored sandboxes need a brief moment before exec.
    await asyncio.sleep(SNAPSHOT_WARMUP_S)
    boot_s = time.perf_counter() - t_boot

    result = PrResult(name=pr_name, sandbox_id=sandbox.sandbox_id, boot_s=boot_s)
    try:
        # Stage the PR's source + tests.
        await _upload_pr(sandbox, pr_dir)

        # Run the test suite. pytest exit codes: 0 = pass, 1 = test failures.
        t_test = time.perf_counter()
        run = await sandbox.exec(f"cd {WORK_DIR} && python3 -m pytest -q --color=no")
        result.test_s = time.perf_counter() - t_test
        result.exit_code = run.exit_code
        stdout = (run.stdout or "").rstrip()
        stderr = (run.stderr or "").rstrip()
        full = stdout + (("\n" + stderr) if stderr else "")
        result.last_lines = full.splitlines()[-6:]
        # Try to pull pytest's one-line summary (e.g. "5 passed in 0.07s")
        for line in reversed(result.last_lines):
            stripped = line.strip()
            if stripped and ("passed" in stripped or "failed" in stripped or "error" in stripped):
                result.summary = stripped
                break
        return result
    finally:
        try:
            await sandbox.delete()
        except Exception as exc:  # noqa: BLE001
            print(f"    warning: delete {sandbox.sandbox_id[:8]} failed: {exc}")


async def main_async(prs: tuple[str, ...] = DEFAULT_PRS) -> int:
    _load_env()

    region = os.environ["ACA_SANDBOXGROUP_REGION"]
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["ACA_RESOURCE_GROUP"]
    sandbox_group = os.environ["ACA_SANDBOX_GROUP"]
    run_id = uuid.uuid4().hex[:8]

    print("=" * 72)
    print("EPHEMERAL CI RUNNER — snapshot-warm-started PR builds")
    print("=" * 72)
    print(f"==> sandbox group : {sandbox_group} ({region})")
    print(f"==> run id        : {run_id}")
    print(f"==> PRs to build  : {', '.join(prs)}")
    print()

    cred = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint=endpoint_for_region(region),
        credential=cred,
        subscription_id=subscription,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    base_sandbox = None
    snapshot_id: str | None = None
    cold_boot_s = 0.0
    setup_s = 0.0
    snap_s = 0.0

    try:
        # ---- Phase 1: cold base + install pytest ----------------------
        print(f"==> Phase 1 — cold-boot base sandbox (disk={DISK})")
        t = time.perf_counter()
        base_poll = await client.begin_create_sandbox(
            disk=DISK,
            labels={"scenario": "developer-workflows", "role": "base", "run": run_id},
        )
        base_sandbox = await base_poll.result()
        cold_boot_s = time.perf_counter() - t
        print(f"    base sandbox: {base_sandbox.sandbox_id}  (boot {cold_boot_s:.1f}s)")

        print("==> Installing pytest...")
        t = time.perf_counter()
        setup = await base_sandbox.exec(SETUP_CMD)
        setup_s = time.perf_counter() - t
        if setup.exit_code != 0:
            print(f"setup failed: {setup.stderr}", file=sys.stderr)
            return 1
        print(f"    pytest installed in {setup_s:.1f}s")

        # ---- Phase 2: snapshot --------------------------------------
        print("==> Phase 2 — snapshotting the warm runner image...")
        t = time.perf_counter()
        snap = await base_sandbox.create_snapshot(name=f"{SNAPSHOT_NAME}-{run_id}")
        snapshot_id = snap.id
        snap_s = time.perf_counter() - t
        print(f"    snapshot id: {snapshot_id}  (created in {snap_s:.1f}s)")

        # Give the snapshot service a beat before we restore from it.
        await asyncio.sleep(5)

        # Base sandbox no longer needed — tear it down so we don't bill for it
        # while the PRs run.
        print(f"==> Tearing down base sandbox {base_sandbox.sandbox_id}...")
        await base_sandbox.delete()
        base_sandbox = None

        # ---- Phase 3: per-PR runs from snapshot, in parallel ---------
        print(f"==> Phase 3 — building {len(prs)} PR(s) in parallel from snapshot...")
        t = time.perf_counter()
        results: list[PrResult] = list(await asyncio.gather(
            *(_run_pr(client, snapshot_id, name, run_id) for name in prs),
        ))
        parallel_s = time.perf_counter() - t

        print()
        print("=" * 72)
        print("CI SUMMARY")
        print("=" * 72)
        print(f"  base cold boot         : {cold_boot_s:5.1f}s")
        print(f"  pip install pytest     : {setup_s:5.1f}s")
        print(f"  snapshot creation      : {snap_s:5.1f}s")
        print(f"  parallel PR phase      : {parallel_s:5.1f}s  (wall)")
        print()
        print(f"  {'PR':<8} {'boot':>6} {'tests':>7} {'verdict':<7} {'summary'}")
        print(f"  {'-'*8} {'-'*6} {'-'*7} {'-'*7} {'-'*40}")
        for r in sorted(results, key=lambda r: r.name):
            verdict = "PASS" if r.passed else "FAIL"
            print(f"  {r.name:<8} {r.boot_s:5.1f}s {r.test_s:6.1f}s {verdict:<7} {r.summary}")

        # Tail of any failed PRs.
        for r in results:
            if not r.passed:
                print()
                print(f"  [{r.name}] FAILED — last {len(r.last_lines)} line(s) of pytest output:")
                for line in r.last_lines:
                    print(f"    {line}")

        total_pass = sum(1 for r in results if r.passed)
        print()
        print(f"  TOTAL: {total_pass} / {len(results)} PRs passed")
        print()

        # Highlight the warm-vs-cold story.
        if results:
            warm_avg = sum(r.boot_s for r in results) / len(results)
            speedup = (cold_boot_s + setup_s) / max(warm_avg, 0.1)
            print(f"  Warm boot mean: {warm_avg:.1f}s vs cold base "
                  f"{cold_boot_s + setup_s:.1f}s ({speedup:.1f}× faster per PR).")

        # CI-style exit: nonzero iff anything failed.
        return 0 if total_pass == len(results) else 2
    finally:
        if base_sandbox is not None:
            try:
                await base_sandbox.delete()
            except Exception as exc:  # noqa: BLE001
                print(f"    warning: base sandbox delete failed: {exc}")
        if snapshot_id:
            print(f"==> delete_snapshot({snapshot_id})")
            try:
                await client.delete_snapshot(snapshot_id)
            except Exception as exc:  # noqa: BLE001
                print(f"    warning: snapshot delete failed: {exc}")
        await client.close()
        await cred.close()


def main() -> int:
    prs = tuple(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_PRS
    return asyncio.run(main_async(prs))


if __name__ == "__main__":
    raise SystemExit(main())
