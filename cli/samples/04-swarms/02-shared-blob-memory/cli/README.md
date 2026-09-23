# Shared-blob memory swarm: `aca` CLI variant

Same scenario as the [Python variant](../../../../../python/samples/04-swarms/02-shared-blob-memory/python/swarm.py), expressed in
bash + the `aca` CLI. The platform-provided durability story is the
same: one `aca sandboxgroup volume create --type AzureBlob` on the
worker group, then every worker (and the aggregator) mounts it at
`/mnt/shared` with one `aca sandbox mount` call. No
`azure-storage-blob`, no SAS, no extra role grants.

```bash
./run.sh
```

Configuration is read from `python/samples/.env` (run
`python python/samples/setup/setup.py` from the repository root once
if you haven't). The script maps setup's subscription and region keys
to the CLI env vars and removes the host's baseline `ACA_SANDBOX_GROUP`
override before switching groups with `aca config sandbox set`. Group
creation attempts to grant the signed-in user Data Owner automatically;
the script also ensures that host grant succeeded on both groups and
grants the orchestrator managed identity access to the worker group.

The full scenario story (cast table, sequence diagram, customer-value
claims, production tips) lives in [`../README.md`](../README.md).

## What this script demonstrates (CLI-specific)

- **`aca config sandbox set`** on the host so subsequent host `aca`
  calls don't need `--group`/`--region`.
- **`aca sandboxgroup volume create --name shared-memory --type AzureBlob`**
  after selecting the worker group with `aca config sandbox set`, no
  storage account to provision or container to wire up.
- **`aca sandbox mount --id $ID --volume $V --path /mnt/shared`**,
  one call per worker (and the aggregator); platform handles identity,
  network, mount semantics.
- **Env-only context inside the orchestrator**: every inner `aca` call
  is parameter-free; `ACA_SANDBOX_GROUP=$WORKER_GROUP` +
  `ACA_SANDBOX_MANAGED_IDENTITY=system` is the entire auth story.

## Status

End-to-end validated against the **Python SDK variant**
(see `python/samples/04-swarms/02-shared-blob-memory/python/swarm.py`) on `westus2`: 4 worker sandboxes each
write a `worker-i.json` checkpoint to the shared volume, then a
separate aggregator sandbox reads them back after the workers are
deleted, π ≈ 3.141 across 4×10⁶ darts.

The CLI variant was audited offline against `aca 1.0.0-preview.4`,
but has **not** been validated end-to-end on this version. The older
`1.0.0-beta.1` in-sandbox managed-identity path previously returned
401; verify token acquisition and worker operations in a live run
before claiming that regression is fixed. `aca auth status` is
diagnostic in the script; a failure there does not stop worker creation.

### Running on Windows

The script targets bash. On Windows, **use Git Bash** with the Windows
`aca.exe`. WSL requires a separately installed Linux `aca` binary;
this variant has not been live-tested on WSL.

The script sets `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL='*'`
so that POSIX paths like `/tmp/swarm.sh` and `/mnt/shared` are
passed through unchanged. Local host-file paths (e.g. the mktemp
upload source) are explicitly converted with `cygpath -w`.
