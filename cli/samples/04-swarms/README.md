# 04 - Swarms (`aca` CLI)

Multi-sandbox swarm scenarios driven by bash + the `aca` CLI. An orchestrator
sandbox uses its group's managed identity to create and drive worker sandboxes,
with no credential ever placed inside the agent.

The validated Python SDK variants and the full scenario writeups (architecture
diagrams, customer-value claims, production tips) live under
[`python/samples/04-swarms`](../../../python/samples/04-swarms).

| Scenario | What it shows |
|----------|---------------|
| [`01-sandbox-inception`](01-sandbox-inception/) | An orchestrator sandbox spawns worker sandboxes via managed identity (Monte Carlo Pi). |
| [`02-shared-blob-memory`](02-shared-blob-memory/) | Workers share an AzureBlob volume mounted at `/mnt/shared`, no storage account to wire up. |
