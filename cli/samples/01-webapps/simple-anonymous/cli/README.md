# Simple anonymous app (`aca` CLI)

One script, sharing the Node app in [`../app/`](../app/):

| Script | What it shows |
|--------|---------------|
| [`run.sh`](run.sh) | `aca sandbox port add --port 8080 --anonymous` - open to the internet |

## Prerequisites

- `aca` CLI installed (`curl -fsSL https://aka.ms/aca-cli-install | sh` on macOS/Linux, `irm https://aka.ms/aca-cli-install-ps | iex` on Windows)
- `python/samples/.env` populated (run `python python/samples/setup/setup.py` from the repo root if you haven't)

## Run

**Bash (macOS / Linux)**
```bash
bash run.sh
```

**PowerShell (Windows)**
```powershell
bash run.sh
```

> On Windows without WSL/git-bash, you can run the Python flavor instead (`cd ../python && python run.py`).

Override the sandbox disk image with `ACA_WEBAPP_DISK=...` (default: `node-22`).
