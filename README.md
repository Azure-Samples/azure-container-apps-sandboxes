# ACA Sandboxes

Secure, isolated compute environments with sub-second startup for agentic workloads.

## Quick start

### Prerequisites

- Azure subscription with permission to create resource groups
- Azure CLI (`az`) installed and logged in
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (installs Python 3.13+ automatically)

### Install and provision

```bash
uv sync
uv run python/samples/setup/setup.py
```

This creates a resource group, a sandbox group, grants your user the data-plane role, and writes `python/samples/.env`. Role assignments take 30-60 seconds to propagate.

Optionally install the [`aca` CLI](https://sandboxes.azure.com/docs/sandboxes/quickstart/setup-cli) for shell-based samples.

### Run your first sample

```bash
cd python/samples/01-webapps/simple-anonymous/python
uv run run.py
```

A sandbox boots, a Node.js app starts, and a public URL is printed. Open it in a browser to see a live system-stats page served from inside the sandbox. From here, pick any sample below.

## Create and manage sandboxes

| Method | Description | Link |
|---|---|---|
| Portal | Browser UI for sandbox groups and sandboxes | [sandboxes.azure.com](https://sandboxes.azure.com) |
| CLI | Shell scripting and automation | [CLI quickstart](https://sandboxes.azure.com/docs/sandboxes/quickstart/setup-cli) |
| Python SDK (Beta) | Programmatic access | [PyPI](https://pypi.org/project/azure-containerapps-sandbox/) |
| Typescript SDK (Beta) | Programmatic access | [NMP](https://www.npmjs.com/package/@azure/containerapps-sandbox) |

## Samples

### Labs (Jupyter notebooks, Python SDK)

| # | Lab | What it shows |
|---|---|---|
| 01 | [getting-started](python/labs/01-getting-started.ipynb) | Full surface end-to-end: create group → sandbox → exec → files → ports → egress → lifecycle → cleanup |
| 02 | [bring-your-own-container](python/labs/02-bring-your-own-container.ipynb) | Sandbox from your own container image, open a port to access its web content |
| 03 | [sandbox-inception](python/labs/03-sandbox-inception.ipynb) | SDK inside a sandbox spawning child sandboxes via managed identity |

### Scenarios (composed use cases)

Each scenario includes a README with architecture notes, prerequisites, and step-by-step instructions.

| # | Scenario | What it shows | Python | CLI |
|---|---|---|---|---|
| 01 | webapps | Web app in a sandbox, anonymous or Entra-gated | [Python](python/samples/01-webapps) | [CLI](cli/samples/01-webapps) |
| 02 | coding-agents | Copilot CLI in a sandbox with deny-default egress | [Python](python/samples/02-coding-agents) | [CLI](cli/samples/02-coding-agents) |
| 03 | code-interpreter | LLM-driven code execution: generate, run, observe, iterate | [Python](python/samples/03-code-interpreter) | - |
| 04 | swarms | Orchestrator coordinating many sandbox workers | [Python](python/samples/04-swarms) | [CLI](cli/samples/04-swarms) |
| 05 | data-processing | Producer/consumer pipelines on shared AzureBlob volumes | [Python](python/samples/05-data-processing) | - |
| 06 | developer-workflows | PR builds, ephemeral CI, on-demand dev environments | [Python](python/samples/06-developer-workflows) | - |
| 07 | computer-use | LLM computer-use agent driving Chrome inside a sandbox | - | - |
| 08 | sandbox-agents | Agent frameworks (OpenAI, Claude, LangChain) using sandboxes as tool-execution backend | [Python](python/samples/08-sandbox-agents) | - |
| 09 | mcp-hosting | Host MCP servers in a sandbox | [Python](python/samples/09-mcp-hosting) | - |
| 10 | connectors-email-triage | Outlook trigger → sandbox → Copilot CLI → Teams triage card | [Python](python/samples/10-connectors-email-triage) | - |
| 11 | connectors-document-automation | SharePoint trigger → sandbox → document extraction → results uploaded | [Python](python/samples/11-connectors-document-automation) | - |

## More resources

- [Documentation](https://sandboxes.azure.com/docs/sandboxes) - concepts, guides, API reference
- [Portal](https://sandboxes.azure.com/) - browser-based management UI
