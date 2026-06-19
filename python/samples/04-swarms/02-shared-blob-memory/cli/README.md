# Shared-blob memory swarm — `aca` CLI variant

Same scenario as the [Python variant](../python/swarm.py), expressed in
bash + the `aca` CLI. The platform-provided durability story is the
same: one `aca sandboxgroup volume create --type AzureBlob` on the
worker group, then every worker (and the aggregator) mounts it at
`/mnt/shared` with one `aca sandbox mount` call. No
`azure-storage-blob`, no SAS, no extra role grants.

```bash
./run.sh
```

Configuration is read from `samples/.env` (run
[`../../../../setup`](../../../../setup) once if you haven't).

The full scenario story (cast table, sequence diagram, customer-value
claims, production tips) lives in [`../README.md`](../README.md).

## What this script demonstrates (CLI-specific)

- **`aca config sandbox set`** on the host so subsequent host `aca`
  calls don't need `--group`/`--region`.
- **`aca --group $WORKER_GROUP sandboxgroup volume create --type AzureBlob`** —
  one command, no storage account to provision, no container to wire up.
- **`aca sandbox mount --id $ID --volume $V --path /mnt/shared`** —
  one call per worker (and the aggregator); platform handles identity,
  network, mount semantics.
- **Env-only context inside the orchestrator** — every inner `aca` call
  is parameter-free; `ACA_SANDBOX_GROUP=$WORKER_GROUP` +
  `ACA_SANDBOX_MANAGED_IDENTITY=system` is the entire auth story.

## Status

End-to-end validated against the **Python SDK variant**
(see `../python/swarm.py`) on `westus2`: 4 worker sandboxes each
write a `worker-i.json` checkpoint to the shared volume, then a
separate aggregator sandbox reads them back after the workers are
deleted — π ≈ 3.141 across 4×10⁶ darts.

The CLI variant uses the **same Azure-side setup**, but relies on
`aca --managed-identity` from inside the orchestrator sandbox.
In `aca` CLI `1.0.0-beta.1`, this path returns 401 when the CLI
requests a data-plane token from the in-sandbox MI proxy — the
managed-identity path works end-to-end through the Python SDK in
the sibling variant. Once the CLI's MI data-plane scope handling
lands, this script runs unchanged.

If you want to run the host-side portion only (provision groups +
grant role + **create the AzureBlob volume** + create orchestrator +
upload `swarm.sh`), the script will perform those steps successfully
and stop at the `aca auth status` call inside the orchestrator.

### Running on Windows

The script targets bash. On Windows, **use Git Bash** (it picks up
the Windows `aca.exe`, which has the full feature set). WSL bash
will use a Linux `aca` binary, which in the current beta lacks
`aca config sandbox set`.

The script sets `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL='*'`
so that POSIX paths like `/tmp/swarm.sh` and `/mnt/shared` are
passed through unchanged. Local host-file paths (e.g. the mktemp
upload source) are explicitly converted with `cygpath -w`.
