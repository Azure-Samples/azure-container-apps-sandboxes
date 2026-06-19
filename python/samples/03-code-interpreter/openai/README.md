# 03 — Code Interpreter — Azure OpenAI

A self-contained Azure OpenAI driver for the code-interpreter loop
described in the [parent README](../README.md). One file
([`python/run.py`](python/run.py)), no agent framework — just
`chat.completions.create` + three function-calling tools.

## What it ships

| File | What it is |
|---|---|
| [`python/run.py`](python/run.py) | The whole demo. ~400 lines. |
| [`python/data/sales.csv`](python/data/sales.csv) | 36-row monthly sales dataset across three channels (Online / Retail / Wholesale). Staged into `/workspace/data/` inside the sandbox. |

## Tools the model can call

| Tool | Purpose |
|---|---|
| `python_exec(code)` | Write `code` to `/tmp/cell-XXXX.py` inside the sandbox, `python3` it, return stdout/stderr/exit_code (each truncated at 4 KB). |
| `read_file(path)` | `sandbox.read_file(path)` → text content. For peeking at intermediate files the model wrote. |
| `download_artifact(path)` | `sandbox.read_file(path)` → host `./out/<basename>`. Use this for plots and other binaries the model wants to surface. |

## Prerequisites

1. The sandbox baseline is provisioned (`uv run python/samples/setup/setup.py`). This writes the `ACA_*` keys to `samples/.env`.
2. An **Azure OpenAI** deployment that supports tool calling — `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `gpt-5`, `o4-mini` all qualify.
3. The following added to `samples/.env`:

   ```bash
   AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
   AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
   AZURE_OPENAI_API_VERSION=2024-10-21    # optional; default is 2024-10-21
   AZURE_OPENAI_API_KEY=<your-key>        # optional; falls back to AAD
   ```

   If `AZURE_OPENAI_API_KEY` is omitted, the script uses
   `DefaultAzureCredential` against the deployment — the caller needs
   the **Cognitive Services OpenAI User** role on the AOAI resource.

## Run it

```bash
cd python

# default prompt: explore sales.csv
uv run --extra openai run.py

# custom prompt
uv run --extra openai run.py "Which months have the highest revenue per marketing dollar, per channel? Save a stacked bar chart of revenue by channel to /workspace/out/efficiency.png."

# override the model deployment for one run
uv run --extra openai run.py --model gpt-4o-mini "..."

# cap the loop tighter
uv run --extra openai run.py --max-turns 8 "..."
```

Plots saved by the model under `/workspace/out/` are auto-downloaded to
`./out/` on the host. Each run uses a fresh sandbox; nothing persists.

## Expected shape of a run

```
========================================================================
CODE INTERPRETER — Azure OpenAI in an ACA sandbox
========================================================================
==> deployment    : gpt-4o-mini
==> sandbox group : ai-apps-samples-group (westus2)
==> run id        : a1b2c3d4
==> prompt        : I've put a sales CSV at /workspace/data/sales.csv ...

==> Booting sandbox (disk=python-3.14)...
    sandbox: 12345678-...
==> Staging data/ into /workspace/data/ ...
    staged sales.csv (1,354 bytes)
==> Installing pandas + matplotlib in the sandbox...

==> Turn 1: model thinking...
    -> python_exec(code="import pandas as pd; df = pd.read_csv(...)")
       │ shape: (36, 5)
       │ columns: ['date', 'channel', 'marketing_spend', 'units_sold', 'revenue']
       (exit=0)
==> Turn 2: model thinking...
    -> python_exec(code="growth = ...")
       │ Wholesale grew 222%, Retail grew 193%, Online grew 229%
       (exit=0)
==> Turn 3: model thinking...
    -> python_exec(code="import matplotlib.pyplot as plt; ...")
       │ Saved /workspace/out/revenue.png (24,103 bytes)
       (exit=0)
==> Turn 4: model thinking...
    -> download_artifact(path='/workspace/out/revenue.png')
       saved -> .../out/revenue.png (24,103 bytes)
==> Turn 5: model thinking...

========================================================================
ANSWER
========================================================================
Online posted the highest 2024 growth (Jan vs Dec), at +229% revenue ...

Saved 1 artifact(s) to .../out:
  - out/revenue.png (24,103 bytes)

(total: 38.4s across 5 turn(s))
==> Deleting sandbox 12345678-...
```

## Notes

- **Model choice matters.** Tool-calling reliability differs across
  AOAI models. `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, and `gpt-5` are good
  defaults. Older `gpt-3.5-turbo` deployments work but require many more
  turns for the same task.
- **Cold-start cost.** First boot installs ~120 MB of pip packages
  (~30 s on `python-3.14`). For repeated demos, follow the "bake the
  disk" tip in the [parent README](../README.md).
- **Single-cell semantics.** Each `python_exec` is a fresh process —
  the model can't rely on previous `import` lines. It learns this fast
  (write helpers as files, re-import) but if you want notebook semantics,
  use a long-lived background interpreter instead. (Possible follow-up
  variant.)
