# `agents-extension-aca-sandboxes`

ACA Sandboxes provider for the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

Run `SandboxAgent` on Azure Container Apps Sandboxes with the same API shape
as the built-in Docker/Unix providers.

```python
from azure.containerapps.sandbox.aio import SandboxGroupClient
from azure.identity.aio import DefaultAzureCredential
from agents import Runner
from agents.run_config import RunConfig
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Shell, Filesystem
from agents_aca_sandboxes import (
    ACASandboxesClient,
    ACASandboxesClientOptions,
)

async def main() -> None:
    cred = DefaultAzureCredential()
    async with SandboxGroupClient(
        "https://management.westus2.azuredevcompute.io",
        cred,
        subscription_id="...",
        resource_group="ai-apps-samples-rg",
        sandbox_group="ai-apps-samples-group",
    ) as group_client:
        sandbox_client = ACASandboxesClient(group_client)
        agent = SandboxAgent(
            name="researcher",
            instructions="Research the ACA Sandboxes guides and answer with citations.",
            capabilities=[Shell(), Filesystem(read_only=False)],
        )
        run_config = RunConfig(
            sandbox=SandboxRunConfig(
                client=sandbox_client,
                options=ACASandboxesClientOptions(disk="ubuntu"),
            ),
        )
        result = await Runner.run(agent, "What's the default egress posture?", run_config=run_config)
        print(result.final_output)
```

## Install (dev)

```bash
pip install -e ".[dev]"
pytest -q
```


