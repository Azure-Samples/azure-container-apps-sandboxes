# Demo 2 — Swarm Research Agent

**Multi-agent parallel research.** Multiple research workers run in parallel,
each in its own fully-isolated ACA microVM, researching different topics.
You specify what to research via command-line arguments.

## What it does

1. Boots N ACA sandboxes concurrently (bounded by `--concurrency`)
2. Each worker clones a GitHub repository for one topic
3. Workers research in parallel based on your questions
4. Aggregator collects findings into a comprehensive report
5. Generates `final-answer.md` with aggregated research

**Note:** Source URLs must be public GitHub repositories. Each worker performs a `git clone` inside its sandbox.

## What it proves

| Platform capability | How the swarm exercises it |
| --- | --- |
| **Fan-out across isolated sandboxes** | N sandboxes spun up in parallel, one per topic |
| **Bounded concurrency** | `--concurrency N` caps in-flight workers (default: 3) |
| **Graceful degradation** | One worker failure doesn't abort swarm; failures surfaced in trace.json |
| **Tenant/session labels** | Every sandbox labeled with `demo=swarm, topic=<key>, run-id=<uuid>` |
| **Runner-managed lifecycle** | Every sandbox torn down automatically on success or failure |

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
cd samples/sandboxes/scenarios/08-sandbox-agents/openai/02-swarm-research-parallel
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

Each `--topic` argument follows the format: **`key|name|source_url|question`**

- **key**: lowercase-alphanumeric-dashes (used for filenames, e.g., `aca-sandboxes`)
- **name**: Display name for the topic (e.g., `Azure Container Apps Sandboxes`)
- **source_url**: **GitHub repository URL** (e.g., `https://github.com/Azure/azure-functions-host`)
- **question**: Research question for the agent

**Important:** `source_url` must be a public GitHub repository URL. Workers use `git clone` inside the sandbox.

```powershell
# Research sandbox implementations (recommended first test)
python swarm_research_agent.py --topic "azure-functions|Azure Functions|https://github.com/Azure/azure-functions-host|What is Azure Functions and how does it work?" --topic "firecracker|Firecracker microVMs|https://github.com/firecracker-microvm/firecracker|How does Firecracker provide lightweight isolation?"

# Research 2 web frameworks
python swarm_research_agent.py --topic "fastapi|FastAPI|https://github.com/fastapi/fastapi|What makes FastAPI performant?" --topic "django|Django|https://github.com/django/django|What are Django's key features?"

# Compare 3 container runtimes (with lower concurrency)
python swarm_research_agent.py --topic "kubernetes|Kubernetes|https://github.com/kubernetes/kubernetes|How does the scheduler work?" --topic "docker|Docker|https://github.com/docker/cli|What are Docker's isolation mechanisms?" --topic "containerd|containerd|https://github.com/containerd/containerd|What are containerd's core responsibilities?" --concurrency 2
```

### 5. View results

```powershell
# View aggregated research report
notepad final-answer.md

# Or open the output directory
explorer .run-output\swarm-<run-id>
```

Typical run: **~60–90s** end-to-end with concurrency 3, **~45-60s** with concurrency 2.

## Example use cases

```powershell
# Research sandbox implementations (recommended first test)
python swarm_research_agent.py \
  --topic "aca-sandboxes|Azure Container Apps Sandboxes|https://github.com/annaji-msft/ai-apps|What are ACA Sandboxes? Include architecture, security, and lifecycle." \
  --topic "firecracker|Firecracker microVMs|https://github.com/firecracker-microvm/firecracker|What is Firecracker and how does it provide lightweight isolation?" \
  --concurrency 2

# Compare ML frameworks
python swarm_research_agent.py --topic "pytorch|PyTorch|https://github.com/pytorch/pytorch|What is PyTorch's autograd system?" --topic "tensorflow|TensorFlow|https://github.com/tensorflow/tensorflow|What are TensorFlow's core components?" --concurrency 2

# Research orchestration platforms
python swarm_research_agent.py --topic "airflow|Apache Airflow|https://github.com/apache/airflow|What are Airflow's key concepts?" --topic "prefect|Prefect|https://github.com/PrefectHQ/prefect|How does Prefect handle workflow orchestration?" --topic "temporal|Temporal|https://github.com/temporalio/temporal|What is Temporal's architecture?"

# Analyze API frameworks
python swarm_research_agent.py --topic "express|Express.js|https://github.com/expressjs/express|What is Express's middleware system?" --topic "fastify|Fastify|https://github.com/fastify/fastify|What makes Fastify fast?" --concurrency 2
```

## Artifacts (per run)

Each invocation writes to `./.run-output/swarm-<run_id>/`:

- `final-answer.md` — aggregated research across all topics
- `summary.json` — structured data (topics, timing, success rate)
- `findings/<topic-key>.md` — each worker's raw output + telemetry

## Example output (truncated)

```
==> ACA sandbox group : your-sandbox-group (westus2)
==> AOAI deployment   : your-deployment-name
==> Demo run id       : a1b2c3d4
==> Topics            : fastapi, django, flask
==> Concurrency       : 3
==> Timeout per worker: 180s
==> Output            : .../02-swarm-research-parallel/.run-output/swarm-a1b2c3d4

    [fastapi   ] -> sandbox up, agent running...
    [django    ] -> sandbox up, agent running...
    [flask     ] -> sandbox up, agent running...
    [fastapi   ] OK in  45.2s
    [django    ] OK in  52.1s
    [flask     ] OK in  38.7s

========================================================================
SWARM COMPLETE
========================================================================
  Successful     : 3 / 3
  Wall-clock time: 62.5s
  Output         : .../02-swarm-research-parallel/.run-output/swarm-a1b2c3d4/final-answer.md

# Research Findings
## FastAPI: Async performance, automatic validation, OpenAPI docs
## Django: Batteries-included, ORM, admin interface, security features
## Flask: Microframework, flexible, extensible through blueprints
```

## Notes

**"OPENAI_API_KEY is not set, skipping trace export"** — This warning is harmless. It's about optional telemetry/tracing to OpenAI's platform. Your Azure OpenAI connection works fine via `AZURE_OPENAI_API_KEY` and doesn't need the separate `OPENAI_API_KEY`. You can safely ignore this message.

## Customizing

- **Different model**: Change `AZURE_OPENAI_DEPLOYMENT` in `.env`
- **Different topics**: Specify any `--topic` arguments
- **Concurrency**: Use `--concurrency N` to control parallel workers
- **Timeout**: Use `--timeout-per-worker N` for longer research sessions

## Where to go next

- **[`../01-deep-research-single/`](../01-deep-research-single)** — Single-agent repo analysis demo
- **[`../sandbox-agent-extension/`](../sandbox-agent-extension)** — The `agents_aca_sandboxes` provider itself
