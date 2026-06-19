# Sandbox inception swarm: `aca` CLI variant

Same scenario as the Python variant, but the orchestration is bash +
the `aca` CLI. The script is structured so that **`aca config`** is
the obvious ergonomic win, neither the host nor the orchestrator pass
`--subscription` / `--resource-group` / `--group` / `--managed-identity`
on individual `aca` calls.

```bash
./run.sh
```

Configuration is read from `samples/.env` (run [`python/samples/setup`](../../../../../python/samples/setup)
once if you haven't).

The full scenario story (architecture diagram, four customer-value
claims, production tips) lives in [`../README.md`](../README.md).

## Status

End-to-end validated against the **Python SDK variant**
(see `python/samples/04-swarms/01-sandbox-inception/python/swarm.py`) on `westus2`, π estimated to ±7×10⁻⁴
across 4 worker sandboxes spawned via managed identity.

The CLI variant uses the same Azure-side setup but relies on
`aca --managed-identity` from inside the orchestrator sandbox.
In `aca` CLI `1.0.0-beta.1`, this path returns 401 when the CLI
requests a data-plane token from the in-sandbox MI proxy, the
managed-identity work end-to-end through the Python SDK in the
sibling variant. Once the CLI's MI data-plane scope handling lands,
this script runs unchanged.

If you want to run the host-side portion only (provision groups +
grant role + create orchestrator + upload `swarm.sh`), the script
will perform those steps successfully and stop at the `aca auth
status` call inside the orchestrator.

### Running on Windows

The script targets bash. On Windows, **use Git Bash** (it picks up
the Windows `aca.exe`, which has the full feature set). WSL bash
will use a Linux `aca` binary, which in the current beta lacks
`aca config sandbox set`.

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

**Host side (driving Group A)**, set the orchestrator group as the
current sandbox context once; every later `aca` call uses it:

```bash
aca config set -s "$ACA_SUBSCRIPTION" -r "$ACA_RESOURCE_GROUP"
aca config sandbox set --group "$ORCH_GROUP"   # auto-detects region too
aca config show                                # printed in run output

aca sandboxgroup identity assign --system-assigned --name "$ORCH_GROUP"
aca sandbox create --disk ubuntu               # implicit --group from config
```

**Sandbox side (driving Group B)**, env vars are the same source of
truth as `aca config`, so a few `export`s flip the orchestrator's
entire context onto the worker group + MI auth:

```bash
export ACA_SUBSCRIPTION=...
export ACA_RESOURCE_GROUP=...
export ACA_SANDBOX_GROUP="$WORKER_GROUP"
export ACA_SANDBOX_MANAGED_IDENTITY=system     # use the group's MI
export ACA_REGION=...

/tmp/aca auth status                           # one-line proof: ARM authed via MI
for i in $(seq 0 $((WORKERS-1))); do
    /tmp/aca sandbox create --disk ubuntu --label worker=$i &
done
wait                                           # parallel fan-out, 4 lines
```

Without `aca config`, the same loop would carry
`--subscription X --resource-group Y --group Z --managed-identity system`
on every line, noisy, error-prone, and obscures the swarm logic. With
config, the loop reads as the intent: *create four worker sandboxes*.

`aca config show` runs **twice** in the script, once on the host,
once inside the orchestrator, and both outputs are printed, so you
see the two contexts side-by-side.
