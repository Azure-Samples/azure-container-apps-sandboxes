# 01-webapps — Web apps in sandboxes

A sandbox is just a Linux VM with a public address you can put in front
of it — so it's a natural home for web apps. This scenario collects
patterns for **running a real HTTP server inside a sandbox and exposing
it to the world**, each one a small, runnable end-to-end example.

| Pattern | What it shows | Status |
|---------|---------------|--------|
| [simple-anonymous](simple-anonymous/) | Hello-world Node.js app on `:8080`, port exposed **anonymously** (open to the internet). Tailwind landing page with live `/proc` + `os` stats. | ✅ ready |
| `authenticated` _(coming soon)_ | Same app, port gated by **Entra ID** (`add_port(..., email=...)`) — only specific emails/tenants reach it. | 📝 planned |

Each pattern is fully self-contained under its own folder:

```
01-webapps/
├── README.md                        ← this file
├── simple-anonymous/
│   ├── README.md
│   ├── app/{server.js, package.json}
│   ├── python/{run.py, requirements.txt, README.md}
│   └── cli/{run.sh, README.md}
└── authenticated/                   ← coming soon
    └── …
```

## Pick a pattern

- **simple-anonymous** — start here. Public-internet `Hello world`, easiest
  thing to demo, no auth setup. Five-line CLI version, five-line SDK
  version, one shared Node app. The landing page proves the box is a
  real VM (live load avg, memory, top processes from `/proc`).
- **authenticated** *(planned)* — the right pattern for anything
  customer-facing. Same app, but `add_port(8080, email="...")` puts the
  Entra ID login in front of it; only that email (or tenant / object id)
  reaches the backend.

## Future patterns we might add

- **streaming** — long-lived SSE / WebSocket connection from the sandbox.
- **python-flask** — same shape but the app inside the sandbox is Python.
- **multi-port** — front-end on `:8080`, API on `:8081`, both exposed
  with different auth modes.
- **per-user-sandbox** — one sandbox per request, labels-based lookup,
  snapshot warm starts.

## Composes with these guides

01 (sandboxes) · 06 (ports) · 07 (files) · 05 (lifecycle) · 11 (labels) · 02 (snapshots)
