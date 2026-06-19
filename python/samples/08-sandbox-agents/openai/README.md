# OpenAI Agents SDK + ACA Sandboxes — first-class provider

This folder is the **realized** version of the long-term direction the
scenario README hints at: a first-class
[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) **sandbox
provider** that runs every tool call inside an isolated
[Azure Container Apps sandbox](https://learn.microsoft.com/azure/container-apps/).

> The pitch: **OpenAI Agents SDK + ACA Sandboxes = a secure agentic platform**
> for any startup.

## What ships here

| Folder | What it is |
| --- | --- |
| [`sandbox-agent-extension/`](sandbox-agent-extension) | `agents_aca_sandboxes` — installable provider package (`BaseSandboxClient` + `BaseSandboxSession` against `azure-containerapps-sandbox`). Unit, live-lifecycle, and end-to-end SandboxAgent tests included. |
| [`01-deep-research-single/`](01-deep-research-single) | **Deep Research Agent** — single `SandboxAgent` clones a GitHub repo into one sandbox and answers a question with file citations. |
| [`02-swarm-research-parallel/`](02-swarm-research-parallel) | **Research Swarm** — host orchestrator fans research subtasks out across N parallel sandboxes, each running its own `SandboxAgent`, then aggregates the findings. |
| [`03-autonomous-swarm/`](03-autonomous-swarm) | **Autonomous Swarm (Harness IN Compute)** — the **supervisor itself** runs inside an ACA sandbox and uses its SystemAssigned Managed Identity for both Azure OpenAI (`Cognitive Services OpenAI User`) and a peer worker sandbox group (`Container Apps SandboxGroup Data Owner`). **No AOAI key, client secret, or user credential ever enters any sandbox.** |


## Why "first-class provider" matters

The OpenAI Agents SDK ships a `SandboxAgent` whose tool calls (Shell,
Filesystem, …) are dispatched to a **sandbox provider** rather than executed
in-process. Built-in providers exist for various sandbox platforms.
This folder adds ACA Sandboxes as a peer — meaning your code looks like:

```python
from agents import Runner
from agents.run_config import RunConfig
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Shell, Filesystem

from agents_aca_sandboxes import ACASandboxesClient, ACASandboxesClientOptions

agent = SandboxAgent(
    name="researcher",
    instructions="...",
    capabilities=[Shell(), Filesystem()],
    model=model,
)

result = await Runner.run(
    agent,
    "research question...",
    run_config=RunConfig(sandbox=SandboxRunConfig(
        client=ACASandboxesClient(group_client),
        options=ACASandboxesClientOptions(
            disk="ubuntu",
            labels={"tenant": tenant_id, "session": session_id},
        ),
        manifest=manifest,   # optional: stage files into the sandbox
    )),
)
```

No `@function_tool` wrappers. No bespoke shell/filesystem tools. The Agents
SDK owns the capability surface (Shell, Filesystem, Browser, …); the
provider owns the transport (create/exec/read/write/delete against the ACA
async SDK).

## Quick start

```powershell
# 1. install the provider (editable) + its dev deps
cd sandbox-agent-extension
python -m pip install -e ".[dev]"
pytest -q                       # unit tests

# 2. (optional) live lifecycle test against your sandbox group
$env:ACA_LIVE_TEST = "1"
pytest -q tests/test_live_lifecycle.py    # ~15s end-to-end

# 3. run the deep research demo
cd ..\01-deep-research-single
python -m pip install -r requirements.txt
python deep_research_agent.py
```

See [`01-deep-research-single/README.md`](01-deep-research-single/README.md) and
[`02-swarm-research-parallel/README.md`](02-swarm-research-parallel/README.md)
for per-demo setup and example output, and
[`sandbox-agent-extension/README.md`](sandbox-agent-extension/README.md) for
the full provider API surface.

## What the demos prove (mapped to platform capabilities)

| Platform capability | Where in the demos |
| --- | --- |
| Agent loop | `Runner.run(SandboxAgent, ...)` in `01-deep-research-single/deep_research_agent.py` |
| Tool execution isolation | every Shell/Filesystem call routes through `ACASandboxesSession._exec_internal` → real ACA microVM |
| Workspace staging | `Manifest(entries={"docs": LocalDir(...)}, extra_path_grants=...)` materializes guides into `/workspace/docs/` before turn 1 |
| Session resume | `ACASandboxesClient.resume(state)` + `ACASandboxesSessionState` round-trip — covered by unit and live tests |
| Lifecycle hygiene | `auto_suspend_seconds=300` on every sandbox + Runner-managed teardown |
| Labels for ownership | `ACASandboxesClientOptions.merged_labels()` stamps `scenario / framework / provider / demo / run-id` defaults plus user labels |
| Secure execution outside the model process | model calls run on your harness; sandboxes only see the tool payloads — no API keys leak to user code |

## Where credentials live

Demos 01 and 02 use AOAI API-key auth in the harness (the **harness** holds the key,
the **sandbox** never sees it):

| Location | Model key present? |
| --- | --- |
| `samples/.env` (gitignored) | ✅ |
| Harness process (this Python script) | ✅ |
| **Sandbox container** | **❌** — the harness makes model calls, not the sandbox |
| Sandbox outbound traffic | ❌ (and pairable with [`guides/08-egress`](../../guides/08-egress) for default-deny) |

Demo 03 (`03-autonomous-swarm/`) goes one step further: it uses
**SystemAssigned Managed Identity** for AOAI auth and runs the
supervisor itself inside a sandbox. There is **no AOAI key anywhere
in the pipeline** — not in `samples/.env`, not in the harness, not in
the sandbox. See its
[README](03-autonomous-swarm/README.md) for the full zero-secret matrix.

## Related guides this composes

- [`guides/01-sandboxes`](../../guides/01-sandboxes) — `begin_create_sandbox`, `exec`
- [`guides/07-files`](../../guides/07-files) — `write_file` / `read_file`
- [`guides/11-labels`](../../guides/11-labels) — `labels=` for tenant/session ownership
- [`guides/05-lifecycle`](../../guides/05-lifecycle) — `auto_suspend_seconds`
- [`guides/08-egress`](../../guides/08-egress) — deny-default + allow rules
- [`guides/10-identity`](../../guides/10-identity) — managed identity for tool-side Azure calls
- [`guides/02-snapshots`](../../guides/02-snapshots) — warm-boot prepared workspaces
