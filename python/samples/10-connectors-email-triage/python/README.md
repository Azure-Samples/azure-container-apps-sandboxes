# 10-connectors-email-triage — local-dev runner

The cloud-deployed variant lives at the parent
[`10-connectors-email-triage`](../README.md) — `azd up` deploys
everything and the receiver Container App handles real Outlook
events. This local runner exists so you can iterate on the **triage
prompt** and the **MCP wiring** without re-running `azd deploy` for
every tweak.

## What it does

`run.py` boots a sandbox the same way the receiver does:

1. Discovers the MCP runtime endpoint by ARM-GET on the
   `mcpserverConfig` you point it at.
2. Boots a fresh Ubuntu sandbox in the configured sandbox group.
3. Installs Copilot CLI.
4. Applies the egress policy: deny default, allow the MCP host,
   stamp `X-API-Key` on outbound MCP requests.
5. Writes the prompt rendered from
   [`../prompts/triage.md`](../prompts/triage.md) into the sandbox.
6. Runs `copilot --allow-all-tools -p @prompt.md`.
7. Tears the sandbox down.

The key difference from the receiver: this runner takes a sample
email payload from a local JSON file, so you can replay the same
scenario over and over without sending an actual email.

## Quick start

```bash
pip install -r requirements.txt

# at minimum: bring up the cloud bits once with `azd up` in the
# scenario root, then export the values the runner needs:
export $(azd env get-values | grep -E '^(CONNECTOR_GATEWAY_ID|TEAMS_MCP_SERVER_CONFIG_NAME|CONNECTOR_GATEWAY_API_KEY)=' | xargs)

# default — use the bundled sample-email.json
python run.py

# different payload
python run.py --email path/to/your-email.json

# render the prompt + print the egress plan without booting anything
python run.py --dry-run
```

## Sample input

[`samples/sample-email.json`](samples/sample-email.json) is the shape
of the **value array element** from the Office 365 V3 "When a new
email arrives" trigger payload — that is the shape the receiver pulls
out of `body.value[]` and processes per email.
