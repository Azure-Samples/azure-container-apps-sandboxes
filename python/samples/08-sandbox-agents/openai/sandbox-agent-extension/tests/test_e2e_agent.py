"""End-to-end test: SandboxAgent on the ACA Sandboxes provider.

This is the truest test of the M2 provider — it runs the real OpenAI Agents
SDK agent loop with our provider plugged in, using a real ACA sandbox and a
real Azure OpenAI deployment (gpt-5.4). The agent is asked a small question
that requires using Shell and Filesystem capabilities; success = it answers.

Gated by ``ACA_LIVE_E2E=1`` so we don't burn tokens on every pytest run.
"""

from __future__ import annotations

import os

import pytest

ACA_LIVE_E2E = os.environ.get("ACA_LIVE_E2E", "").strip().lower() in {"1", "true", "yes"}

pytestmark = pytest.mark.skipif(
    not ACA_LIVE_E2E,
    reason="Set ACA_LIVE_E2E=1 to enable end-to-end SandboxAgent test "
    "(requires AOAI credentials + ACA sandbox group)",
)


async def test_sandbox_agent_runs_against_aca() -> None:
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.identity.aio import DefaultAzureCredential
    from openai import AsyncAzureOpenAI

    from agents import Agent, ModelSettings, Runner
    from agents.models.openai_responses import OpenAIResponsesModel
    from agents.run_config import RunConfig
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Filesystem, Shell

    from agents_aca_sandboxes import (
        ACASandboxesClient,
        ACASandboxesClientOptions,
        load_config,
    )

    cfg = load_config()

    aoai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    aoai_deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    aoai_api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    aoai_key = os.environ["AZURE_OPENAI_API_KEY"]

    aoai_client = AsyncAzureOpenAI(
        azure_endpoint=aoai_endpoint,
        api_version=aoai_api_version,
        api_key=aoai_key,
    )
    model = OpenAIResponsesModel(model=aoai_deployment, openai_client=aoai_client)

    cred = DefaultAzureCredential()
    try:
        async with SandboxGroupClient(
            endpoint=cfg.endpoint,
            credential=cred,
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            sandbox_group=cfg.sandbox_group,
        ) as gc:
            sandbox_client = ACASandboxesClient(gc)
            agent: Agent = SandboxAgent(
                name="m2-smoke-agent",
                instructions=(
                    "You are running inside an isolated Azure Container Apps sandbox. "
                    "When asked, use the shell tool to inspect the environment and the "
                    "filesystem tool to write a small marker file at "
                    "/workspace/m2_agent_done.txt before answering."
                ),
                capabilities=[Shell(), Filesystem()],
                model=model,
                model_settings=ModelSettings(),
            )
            run_config = RunConfig(
                sandbox=SandboxRunConfig(
                    client=sandbox_client,
                    options=ACASandboxesClientOptions(
                        disk="ubuntu",
                        labels={"test": "m2-e2e-agent"},
                    ),
                ),
            )
            result = await Runner.run(
                agent,
                "What Linux kernel version is this sandbox running? "
                "Use uname, write /workspace/m2_agent_done.txt with the version, "
                "then answer.",
                run_config=run_config,
                max_turns=8,
            )
            print(f"==> final_output: {result.final_output[:400]}")
            assert result.final_output
    finally:
        await cred.close()
        await aoai_client.close()
