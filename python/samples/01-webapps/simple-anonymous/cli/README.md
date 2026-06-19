# Simple anonymous app — `aca` CLI

One script, sharing the Node app in [`../app/`](../app/):

| Script | What it shows |
|--------|---------------|
| [`run.sh`](run.sh) | `aca sandbox port add --port 8080 --anonymous` — open to the internet |

## Run

```bash
bash run.sh
```

Reads configuration from `samples/.env`. Override the disk image with
`ACA_WEBAPP_DISK=...` (default: `node-22`).
