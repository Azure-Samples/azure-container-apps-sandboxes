# simple-anonymous: the "hello world" web app pattern

The simplest possible shape: upload a small Node.js app into a fresh
sandbox, start it in the background, expose port 8080 **anonymously**
(open to the internet), and verify the response from both inside the
sandbox and from the host machine.

The page that comes back proves the sandbox is a real Linux VM, live
system info, live CPU/memory/process stats read from `/proc` + `os`,
and a get-started panel for visitors to spin up their own.

> Part of [scenarios/01-webapps](../README.md). See sibling patterns
> there for authenticated (Entra-gated) and other web-app shapes.

## What you get

- A small Node.js HTTP server (in [`app/`](app/)) with:
  - `GET /` → a Tailwind-styled landing page with a "Hello from a
    sandbox" hero and live system stats read from `/proc` + `os`.
  - `GET /healthz` → `{ "status": "ok" }` (JSON liveness probe)
  - `GET /api/hello` → `{ "message", "hostname", "uptime", "pid" }`
  - `GET /api/info` → `{ "node", "platform", "arch", "cpus", "memoryMB", "startedAt" }`
  - `GET /api/sysinfo` → uname-style: kernel, distro, CPU model, IP, …
  - `GET /api/stats` → live `loadavg`, memory, process count, uptime
  - `GET /api/processes` → top 10 by RSS, read from `/proc/[0-9]*`
- An `aca` CLI driver (`run.sh`) that uploads the app, starts it, and exposes the port.
- Bounded readiness polling (no fragile `sleep N`) and JSON-shape
  assertions on every endpoint.
- A try/finally cleanup that removes the port before deleting the sandbox.

## Prerequisites

- `python/samples/.env` populated (run `python python/samples/setup/setup.py` from the repo root if you haven't yet)

## Run it

```bash
cd cli
bash run.sh
```

Reads configuration from `samples/.env`. Override the disk image with
`ACA_WEBAPP_DISK=...` (default: `node-22`).

> Prefer the SDK? See the Python version under
> [`python/samples/01-webapps/simple-anonymous`](../../../../python/samples/01-webapps/simple-anonymous).

## Production tips

- **Anonymous = open to the internet.** Don't lean on URL obscurity,
  remove ports promptly, and never serve secrets from a demo endpoint.
  For customer-facing apps, use the sibling `authenticated` pattern.
- **Bake the disk.** Pre-install your dependencies into a custom disk
  image ([guide 03](../../../guides/03-disks/README.md)) so startup is
  "boot", not "boot + npm install".
- **One sandbox per tenant/user.** Tag with `labels=`
  ([guide 11](../../../guides/11-labels/README.md)) so you can find the
  right one with `list_sandboxes(labels=...)`.
- **Snapshots for warm starts.** Snapshot post-build
  ([guide 02](../../../guides/02-snapshots/README.md)) and resume into
  it on each request, much faster than a cold boot.
- **Auto-suspend / auto-delete.** Use `AutoSuspendPolicy`
  ([guide 05](../../../guides/05-lifecycle/README.md)) so idle sandboxes
  don't burn quota.
- **Egress lockdown.** If the app shouldn't reach the internet,
  `set_egress_default("Deny")` and allow only the hosts it needs
  ([guide 08](../../../guides/08-egress/README.md)).

## Layout

```
simple-anonymous/
├── README.md              ← this file
├── app/                   ← Node app deployed by the CLI flow
│   ├── server.js
│   └── package.json
└── cli/
    ├── README.md
    └── run.sh
```
