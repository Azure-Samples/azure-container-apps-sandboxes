# Sandbox inception swarm — Python SDK variant

Provisions two per-run sandbox groups (orchestrator + workers), grants
the orchestrator's system-assigned MI `Data Owner` on the worker
group, boots an orchestrator sandbox, and dispatches Monte Carlo Pi
across N=4 worker sandboxes via `ManagedIdentityCredential` +
`asyncio.gather` from **inside** the orchestrator.

```bash
pip install -r requirements.txt
python swarm.py
```

Configuration is read from `samples/.env` (run [`../../../../setup`](../../../../setup)
once if you haven't).

The full scenario story lives in [`../README.md`](../README.md).
