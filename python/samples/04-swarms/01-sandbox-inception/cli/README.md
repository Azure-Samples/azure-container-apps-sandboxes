# Sandbox inception swarm — `aca` CLI variant

Same scenario as the Python variant, but the orchestration is bash +
the `aca` CLI. The script is structured so that **`aca config`** is
the obvious ergonomic win — neither the host nor the orchestrator pass
`--subscription` / `--resource-group` / `--group` / `--managed-identity`
on individual `aca` calls.

```bash
./run.sh
```

Configuration is read from `samples/.env` (run [`../../../../setup`](../../../../setup)
once if you haven't).

The full scenario story (architecture diagram, four customer-value
claims, production tips) lives in [`../README.md`](../README.md).

## Status

End-to-end validated against the **Python SDK variant**
(see `../python/swarm.py`) on `westus2` — π estimated to ±7×10⁻⁴
across 4 worker sandboxes spawned via managed identity.

The CLI variant uses the same Azure-side setup but relies on
`aca --managed-identity` from inside the orchestrator sandbox.
In `aca` CLI `1.0.0-beta.1`, this path returns 401 when the CLI
requests a data-plane token from the in-sandbox MI proxy — the
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
