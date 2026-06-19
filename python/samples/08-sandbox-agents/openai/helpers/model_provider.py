"""Wire an Azure OpenAI deployment into the OpenAI Agents SDK.

Read configuration from the environment (``AZURE_OPENAI_*``) and return a
fully-constructed :class:`OpenAIResponsesModel` so the demo can drop it
straight into ``Agent(model=...)``.

On import, walk up the directory tree to find ``samples/.env`` and populate
``os.environ`` from it (without overwriting existing keys). This matches the
provider's own ``_config._hydrate_env_from_file`` behaviour and lets demos
work after a single ``samples/.env`` setup, with no per-demo ``.env`` copying
required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _hydrate_samples_env(start: Path | None = None, *, levels: int = 8) -> None:
    """Populate ``os.environ`` from the nearest ``samples/.env`` ancestor."""

    here = (start or Path(__file__).resolve()).parent
    for _ in range(levels):
        candidate = here / "samples" / ".env"
        if candidate.is_file():
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return
        if here.parent == here:
            return
        here = here.parent


_hydrate_samples_env()


@dataclass(frozen=True)
class AzureOpenAIEnv:
    endpoint: str
    deployment: str
    api_version: str
    api_key: str | None


def load_azure_openai_env() -> AzureOpenAIEnv:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip() or None

    missing = [
        name
        for name, val in (
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_DEPLOYMENT", deployment),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Azure OpenAI configuration missing: " + ", ".join(missing) +
            ". Populate samples/.env or set them in the environment."
        )
    return AzureOpenAIEnv(
        endpoint=endpoint,
        deployment=deployment,
        api_version=api_version,
        api_key=api_key,
    )


def build_azure_openai_model(env: AzureOpenAIEnv | None = None):
    """Return an :class:`OpenAIResponsesModel` bound to the AOAI deployment.

    Prefers API key auth when ``AZURE_OPENAI_API_KEY`` is set; otherwise falls
    back to AAD via :class:`azure.identity.DefaultAzureCredential` (the caller
    needs the ``Cognitive Services OpenAI User`` role on the account).
    """

    cfg = env or load_azure_openai_env()
    # Imported here so test/import time stays cheap and the package as a whole
    # remains usable without openai-agents installed (e.g. corpus tests).
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai import AsyncAzureOpenAI

    if cfg.api_key:
        client = AsyncAzureOpenAI(
            azure_endpoint=cfg.endpoint,
            api_version=cfg.api_version,
            api_key=cfg.api_key,
        )
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        client = AsyncAzureOpenAI(
            azure_endpoint=cfg.endpoint,
            api_version=cfg.api_version,
            azure_ad_token_provider=token_provider,
        )

    return OpenAIResponsesModel(model=cfg.deployment, openai_client=client), client
