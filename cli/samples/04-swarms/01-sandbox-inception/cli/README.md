# Sandbox inception swarm: `aca` CLI variant

Same scenario as the Python variant, but the orchestration is bash +
the `aca` CLI. The script is structured so that **`aca config`** is
the obvious ergonomic win: host sandbox operations use a configured
group, while the orchestrator's worker operations use environment
variables. Cross-group ARM commands (identity and role assignment)
still use `--group` to name their target.

```bash
./run.sh
```

Configuration is read from `python/samples/.env` (run
`python python/samples/setup/setup.py` from the repository root once
if you haven't). The script maps setup's `AZURE_SUBSCRIPTION_ID` and
`ACA_SANDBOXGROUP_REGION` to the CLI's `ACA_SUBSCRIPTION` and `ACA_REGION`.
It removes the host's `ACA_SANDBOX_GROUP` env override before switching
groups with `aca config sandbox set`, because env takes precedence over
saved config. Group creation attempts to grant the signed-in user Data
Owner automatically; the script also ensures that host grant succeeded
and grants the orchestrator managed identity access to the worker group.

The full scenario story (architecture diagram, four customer-value
claims, production tips) lives in [`../README.md`](../README.md).

## Status

End-to-end validated against the **Python SDK variant**
(see `python/samples/04-swarms/01-sandbox-inception/python/swarm.py`) on `westus2`, π estimated to ±7×10⁻⁴
across 4 worker sandboxes spawned via managed identity.

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
so that POSIX paths like `/tmp/swarm.sh` are passed through
unchanged. Local host-file paths (e.g. the mktemp upload source)
are explicitly converted with `cygpath -w`.

---

## CLI variant: `aca config` is the showcase

The CLI variant is built so that **`aca config`** is the obvious win
over passing `--subscription` / `--resource-group` / `--group` /
`--managed-identity` on every line. There are two distinct contexts in
this swarm, host driving Group A, sandbox driving Group B, and
config makes each one implicit.

**Host side (driving Group A)**, allow the saved config to select the
orchestrator group for sandbox commands. The setup `.env` includes
`ACA_SANDBOX_GROUP`, so first remove that higher-priority env override:

```bash
export ACA_SUBSCRIPTION="${ACA_SUBSCRIPTION:-$AZURE_SUBSCRIPTION_ID}"
export ACA_REGION="${ACA_REGION:-$ACA_SANDBOXGROUP_REGION}"
unset ACA_SANDBOX_GROUP
aca config sandbox set --group "$ORCH_GROUP" --region "$ACA_REGION"
aca config show

aca sandboxgroup identity assign --group "$ORCH_GROUP" --system-assigned
aca sandbox create --disk ubuntu               # implicit --group from config
```

**Sandbox side (driving Group B)**, env vars override saved config and
select the worker group and managed identity for sandbox operations:

```bash
export ACA_SUBSCRIPTION=...
export ACA_RESOURCE_GROUP=...
export ACA_SANDBOX_GROUP="$WORKER_GROUP"
export ACA_SANDBOX_MANAGED_IDENTITY=system     # use the group's MI
export ACA_REGION=...

aca auth status                                # diagnostics for ARM and data-plane auth
for i in $(seq 0 $((WORKERS-1))); do
    aca sandbox create --disk ubuntu --label worker=$i &
done
wait                                           # parallel fan-out, 4 lines
```

Without config or env defaults, the same loop would carry
`--subscription X --resource-group Y --group Z --managed-identity system`
on every line. Commands that intentionally target another group,
such as the host's worker-group role grant, still specify `--group`.

The script prints `aca config show` on the host and the effective
worker-context env vars inside the orchestrator.
