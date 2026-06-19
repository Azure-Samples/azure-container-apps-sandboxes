# Demo 1 — Deep Research Agent

**Single-agent repo analysis.** A `SandboxAgent` clones any public GitHub repository into an isolated ACA microVM, explores the code and documentation, and answers questions about it.

## What it does

1. Boots a fresh ACA sandbox via the `agents_aca_sandboxes` provider
2. Agent clones the specified GitHub repo into `/workspace/repo/` using git
3. Runs a `SandboxAgent` with **Shell** and **Filesystem** capabilities
4. Agent explores the codebase, reads docs/code, and synthesizes an answer
5. Returns answer with file citations from the cloned repo
6. Runner tears down the sandbox automatically when complete

## Prerequisites

Before running this demo, ensure you have:

✅ **Azure CLI** installed and authenticated (`az login`)  
✅ **ACA Sandboxes** configured - Run `aca config show` to verify:
   - Subscription, resource group, and sandbox group configured
   - Or set these values in `.env` (see step 2 below)  
✅ **Azure OpenAI** deployment with:
   - Endpoint URL
   - Deployment name  
   - API key  
✅ **Python 3.10+** installed

**Need help setting up ACA Sandboxes?** See [python/samples/setup](../../../setup/)

## Quick Start

### 1. Navigate to demo folder
```powershell
cd samples/sandboxes/scenarios/08-sandbox-agents/openai/01-deep-research-single
```

### 2. Set up environment variables

This demo (and the provider package) auto-discovers `samples/.env` at the
repo's `samples/` root by walking up the directory tree. You only need to
populate it **once** for all samples in this repo.

```powershell
# From the repo root
notepad samples\.env
```

Add (or update) the following keys. `samples/.env` is gitignored.

```bash
# Azure OpenAI (required)
AZURE_OPENAI_ENDPOINT=https://<your-apim-or-openai-resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_API_KEY=<your-key>

# ACA Sandboxes (optional — falls back to `aca config show`)
ACA_SUBSCRIPTION=<your-subscription-id>
ACA_RESOURCE_GROUP=<your-resource-group>
ACA_SANDBOX_GROUP=<your-sandbox-group>
ACA_REGION=westus2
```

The per-demo `.env.example` is included as a reference for which keys the
demo reads — you don't need to copy it locally.

**Where to find these values:**
- **Azure OpenAI**: From Azure Portal → Your OpenAI resource → Keys and Endpoint
- **ACA Sandboxes**: Run `aca config show` to see your configured values
- If you don't have ACA sandboxes set up yet, see the [main README](../README.md)

### 3. Install dependencies

```powershell
# Upgrade pip (recommended)
python -m pip install --upgrade pip

# Install all dependencies including the ACA sandboxes provider
python -m pip install -r requirements.txt
```

This installs:
- `openai-agents` — OpenAI Agents SDK
- `azure-containerapps-sandbox` — ACA Sandboxes SDK
- `agents_aca_sandboxes` — The provider extension (from `../sandbox-agent-extension/`)

### 4. Run the demo

```powershell
# Default: analyzes this ai-apps repo to learn about ACA Sandboxes
python deep_research_agent.py "What are ACA Sandboxes and their key features?"

# Analyze any GitHub repo
python deep_research_agent.py \
  --repo https://github.com/kubernetes/kubernetes \
  "How does the Kubernetes scheduler work?"
```

### 5. View results

Results are printed directly to the console with the answer formatted in a nice box.

To save results to a file, redirect output:
```powershell
python deep_research_agent.py "What are ACA Sandboxes?" > research_report.md
```

Typical run: **~60–120s** depending on repo size and question complexity.

## Example prompts

See [`EXAMPLES.md`](EXAMPLES.md) for 8 ready-to-run examples. Quick samples:

```powershell
# Analyze OpenAI Agents SDK capabilities
python deep_research_agent.py --repo https://github.com/openai/openai-agents-python "What are the main agent capabilities provided by this SDK? List them with examples."

# Analyze Kubernetes scheduler architecture
python deep_research_agent.py --repo https://github.com/kubernetes/kubernetes "How does the Kubernetes scheduler work? Explain the scheduling algorithm and key components."

# Analyze FastAPI performance features
python deep_research_agent.py --repo https://github.com/fastapi/fastapi "What makes FastAPI fast? List the performance optimizations and architectural decisions."

# Analyze PyTorch autograd system
python deep_research_agent.py --repo https://github.com/pytorch/pytorch "How does PyTorch's autograd system work? Explain the automatic differentiation mechanism."
```

## Example output

```
==> ACA sandbox group : ai-apps-samples-group (westus2)
==> AOAI deployment   : gpt-5.4
==> Demo run id       : a1b2c3d4
==> Repository        : https://github.com/openai/openai-agents-python

ANSWER
------
The OpenAI Agents SDK provides several key capabilities:

1. **SandboxAgent** - Runs in isolated code execution environments
2. **Shell capability** - Execute bash commands safely
3. **Filesystem capability** - Read/write files with sandboxing
4. **Streaming responses** - Real-time output as agent works
5. **Custom tool integration** - Extend with domain-specific tools

Citations:
- repo/README.md:15-42
- repo/src/openai_agents/agent.py:89-156
- repo/examples/sandbox_agent.py:12-45
```

## What it proves

- **Secure code analysis**: Agent runs in isolated ACA microVM, can't affect host
- **Any public repo**: Works with any GitHub repository URL
- **Real exploration**: Agent uses git, grep, cat to explore repos authentically
- **File citations**: Returns precise file paths and line numbers from repo
- **Auto cleanup**: Sandbox tears down automatically, no manual management

## Notes

**"OPENAI_API_KEY is not set, skipping trace export"** — This warning is harmless. It's about optional telemetry/tracing to OpenAI's platform. Your Azure OpenAI connection works fine via `AZURE_OPENAI_API_KEY` and doesn't need the separate `OPENAI_API_KEY`. You can safely ignore this message.

## Customizing

- **Different model**: Change `AZURE_OPENAI_DEPLOYMENT` in `.env`
- **Different repos**: Use `--repo` flag with any public GitHub URL
- **Tighter sandboxes**: Pass options like `disk`, `cpu`, `memory` via `ACASandboxesClientOptions`

## Where to go next

- **[`../sandbox-agent-extension/`](../sandbox-agent-extension)** — The `agents_aca_sandboxes` provider itself
- **[`../02-swarm-research-parallel/`](../02-swarm-research-parallel)** — Multi-agent parallel swarm research
