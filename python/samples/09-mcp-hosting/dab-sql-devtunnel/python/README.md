# dab-sql-devtunnel — Python SDK driver

```bash
uv run run.py
```

Configuration comes from `samples/.env` (written by
`python/samples/setup/setup.py`).

**One-time interactive step:** the script pauses and prints a Dev
Tunnels device-code login URL + code. Open it in your browser, sign in
with any free Microsoft or GitHub account, then return to the terminal
— the token is cached inside the sandbox for the rest of the run.
You have 15 minutes to complete the sign-in.

See the [pattern README](../README.md) for full architecture, MCP tool
catalog, query examples, and verification details.
