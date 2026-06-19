# Simple anonymous app — Python SDK

One script, sharing the Node app in [`../app/`](../app/):

| Script | What it shows |
|--------|---------------|
| [`run.py`](run.py) | `add_port(8080, anonymous=True)` — open to the internet; host-side curl returns 200 + HTML landing page |

## Run

```bash
pip install -r requirements.txt
python run.py
```

Reads configuration from `samples/.env`. Override the disk image with
`ACA_WEBAPP_DISK=...` (default: `node-22`).
