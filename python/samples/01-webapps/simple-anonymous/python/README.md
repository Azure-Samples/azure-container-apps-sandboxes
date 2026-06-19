# Simple anonymous app (Python SDK)

One script, sharing the Node app in [`../app/`](../app/):

| Script | What it shows |
|--------|---------------|
| [`run.py`](run.py) | `add_port(8080, anonymous=True)` - open to the internet; host-side curl returns 200 + HTML landing page |

## Prerequisites

- `python/samples/.env` populated (run `uv run python/samples/setup/setup.py` from the repo root if you haven't)

## Run

```bash
uv run run.py
```

Override the sandbox disk image with `ACA_WEBAPP_DISK=...` (default: `node-22`).
