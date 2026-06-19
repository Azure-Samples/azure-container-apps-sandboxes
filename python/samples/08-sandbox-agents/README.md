# 08 — Sandbox agents

Wire **agent frameworks** to an Azure Container Apps sandbox as their
execution environment. The agent loop (model calls, planning, tool
routing) stays in your harness; tool execution — shell commands,
file I/O, ports — lives in a fresh sandbox per session.

This is a different shape from [`02-coding-agents`](../02-coding-agents):

| Scenario | What runs in the sandbox | Who drives the model |
|---|---|---|
| `02-coding-agents` | A coding-agent CLI binary (`copilot`, `claude`, `codex`) | The CLI inside the sandbox |
| `08-sandbox-agents` | Shell commands + files emitted by the agent framework | Your harness process outside the sandbox |

## Supported frameworks

| Folder | Framework | Integration today | Status |
|---|---|---|---|
| [`openai/`](openai) | [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | **First-class sandbox provider** (`agents_aca_sandboxes`) — `SandboxAgent` runs every Shell/Filesystem tool call inside an ACA microVM. Three live demos: [single-agent Deep Research](openai/01-deep-research-single), [parallel Research Swarm](openai/02-swarm-research-parallel), and [Autonomous Swarm (Harness IN Compute)](openai/03-autonomous-swarm) — the supervisor itself runs inside a sandbox and uses its Managed Identity for both AOAI and a peer worker group (zero AOAI key in any sandbox). | ✅ provider + 3 demos |

Each subfolder ships:

- For `openai/`: an installable [`sandbox-agent-extension/`](openai/sandbox-agent-extension)
  provider package plus three runnable demos:
  [`01-deep-research-single/`](openai/01-deep-research-single),
  [`02-swarm-research-parallel/`](openai/02-swarm-research-parallel), and
  [`03-autonomous-swarm/`](openai/03-autonomous-swarm) (harness-in-compute,
  zero-secret).
- For the placeholders: a small reusable adapter module + `agent.py` +
  `README.md`, following the same shape as the
  realized OpenAI variant.

## The shared pattern

Every framework above splits the same way:

```
+----------------------+         +-----------------------+
|   Harness (yours)    |         |   Compute (sandbox)   |
|----------------------|         |-----------------------|
|  - model calls       |  exec   |  - shell commands     |
|  - planning loop     | ------> |  - files / artifacts  |
|  - tool routing      | <------ |  - exposed ports      |
|  - approvals / audit | stdout  |  - mounted volumes    |
+----------------------+         +-----------------------+
        ^                                  ^
        |                                  |
   your trusted infra                ACA sandbox group
                                     (deny-default egress,
                                      MI, snapshots, etc.)
```

Today, every variant exposes the same three primitives to the agent
on top of `azure.containerapps.sandbox`:

| Primitive | ACA SDK call |
|---|---|
| Run a shell command | `sandbox.exec(cmd)` → `(stdout, stderr, exit_code)` |
| Upload a file | `sandbox.write_file(path, bytes)` |
| Download a file | `sandbox.read_file(path)` |

Each framework wraps those three primitives in whatever shape its
agent loop expects (`@function_tool` for OpenAI, a sandbox-client
class for Claude Managed Agents, a `BaseSandbox` subclass for Deep
Agents).

## Forward-looking: peers join the provider tier

OpenAI's `SandboxAgent` + `BaseSandboxClient` protocol is GA today, and
[`openai/`](openai) ships a first-class ACA provider against it. Anthropic's
self-hosted Managed Agents sandbox client and LangChain's
`SandboxBackendProtocol` are headed the same way; once their interfaces
stabilize we'll publish `claude-managed-agents-aca` and `deepagents-aca`
the same shape. The same three primitives shown here (`exec`,
`write_file`, `read_file`) are what those packages will expose internally.

## Why ACA sandboxes for this

- **Provider-neutral interfaces.** All three frameworks define
  sandbox interfaces explicitly so providers are interchangeable.
- **Deny-default egress.** Pair with
  [`guides/08-egress`](../../guides/08-egress) to lock down what the
  agent can reach on the network — model calls leave from your
  harness, so the sandbox itself can run with no outbound paths.
- **Managed identity for tool-side Azure calls.** Pair with
  [`guides/10-identity`](../../guides/10-identity) to give
  sandbox-side tools an identity for Cosmos / Storage / Key Vault
  without putting keys in the workload.
- **Snapshots for resumable work.** Pair with
  [`guides/02-snapshots`](../../guides/02-snapshots) to pause an
  agent session for human review and resume later from the exact
  filesystem state.

## Prerequisites

- Shared sandbox baseline provisioned with `uv run python/samples/setup/setup.py`.
- Framework-specific model auth (OpenAI / Azure OpenAI key, or
  Anthropic API key) — see each subfolder's README.

## How to run

```bash
cd openai/01-deep-research-single
uv run --extra agents deep_research_agent.py
```

## What it composes

- [guides/01-sandboxes](../../guides/01-sandboxes) — `begin_create_sandbox`, `exec`
- [guides/07-files](../../guides/07-files) — `write_file` / `read_file`
- (Optional) [guides/08-egress](../../guides/08-egress) — deny-default + host allow rules
- (Optional) [guides/10-identity](../../guides/10-identity) — managed identity for tool-side Azure calls

## Production tips

- **One sandbox per session, not per turn.** The sandbox is
  stateful; reusing it across turns is the whole point. Don't tear
  it down between model calls.
- **Set `auto_suspend_seconds`.** If the agent goes idle waiting on
  human approval, suspend the sandbox to stop billing for idle
  compute. The next tool call resumes it.
- **Label by session ID.** Pair with
  [`guides/11-labels`](../../guides/11-labels) so you can
  `list_sandboxes(labels={"session": session_id})` for janitor jobs
  that reap orphans.
- **Don't put framework API keys in the sandbox.** The harness has
  them; the sandbox doesn't need them. The frameworks deliberately
  send only the *tool* call into the sandbox, not the model
  credential — keep it that way.
