"""Smoke tests for the M1 provider skeleton.

These tests do **not** talk to Azure. They verify that the package installs,
its public API is importable, and the polymorphic options/state registries
work — exactly the criteria M1 promises in plan.md.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from agents.sandbox.manifest import Manifest
from agents.sandbox.session.sandbox_client import BaseSandboxClientOptions
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import NoopSnapshot

import agents_aca_sandboxes
from agents_aca_sandboxes import (
    ACASandboxesClient,
    ACASandboxesClientOptions,
    ACASandboxesSession,
    ACASandboxesSessionState,
)
from agents_aca_sandboxes.models import DEFAULT_LABELS


def test_top_level_imports() -> None:
    assert agents_aca_sandboxes.__version__
    for name in (
        "ACASandboxesClient",
        "ACASandboxesClientOptions",
        "ACASandboxesSession",
        "ACASandboxesSessionState",
    ):
        assert hasattr(agents_aca_sandboxes, name), f"missing public export: {name}"


def test_options_default_type_and_labels() -> None:
    opts = ACASandboxesClientOptions()
    assert opts.type == "aca_sandboxes"
    assert opts.disk == "ubuntu"
    assert opts.merged_labels() == DEFAULT_LABELS


def test_options_label_merge_overrides() -> None:
    opts = ACASandboxesClientOptions(labels={"tenant": "demo", "scenario": "user-overridden"})
    merged = opts.merged_labels()
    assert merged["scenario"] == "user-overridden"
    assert merged["tenant"] == "demo"
    assert merged["framework"] == "openai"


def test_options_polymorphic_parse() -> None:
    """``BaseSandboxClientOptions.parse(payload)`` must dispatch by ``type``."""

    payload = {"type": "aca_sandboxes", "disk": "ubuntu", "exposed_ports": [8080]}
    parsed = BaseSandboxClientOptions.parse(payload)
    assert isinstance(parsed, ACASandboxesClientOptions)
    assert parsed.disk == "ubuntu"
    assert parsed.exposed_ports == (8080,)


def _make_state() -> ACASandboxesSessionState:
    return ACASandboxesSessionState(
        type="aca_sandboxes",
        session_id=uuid4(),
        snapshot=NoopSnapshot(id="noop-test"),
        manifest=Manifest(),
        sandbox_id="sb-test-1",
        sandbox_group="ai-apps-samples-group",
        resource_group="ai-apps-samples-rg",
        subscription_id="00000000-0000-0000-0000-000000000000",
        region="westus2",
    )


def test_state_polymorphic_parse() -> None:
    """``SandboxSessionState.parse(payload)`` must dispatch by ``type``."""

    state = _make_state()
    raw = state.model_dump(mode="json")
    parsed = SandboxSessionState.parse(raw)
    assert isinstance(parsed, ACASandboxesSessionState)
    assert parsed.sandbox_id == "sb-test-1"
    assert parsed.region == "westus2"


def _make_client_without_azure() -> ACASandboxesClient:
    client = ACASandboxesClient.__new__(ACASandboxesClient)
    client._group_client = None  # type: ignore[attr-defined]
    client._instrumentation = None  # type: ignore[attr-defined]
    client._dependencies = None  # type: ignore[attr-defined]
    return client


def test_client_deserialize_session_state_roundtrip() -> None:
    """The provider's own deserializer should accept the JSON dump."""

    client = _make_client_without_azure()
    state = _make_state()
    raw = state.model_dump(mode="json")
    restored = client.deserialize_session_state(raw)
    assert isinstance(restored, ACASandboxesSessionState)
    assert restored.sandbox_id == "sb-test-1"


def test_session_from_state_constructs_inner() -> None:
    """``ACASandboxesSession.from_state`` is required for resume in M2."""

    state = _make_state()
    inner = ACASandboxesSession.from_state(state)
    assert isinstance(inner, ACASandboxesSession)
    assert inner.state.sandbox_id == "sb-test-1"


def test_create_calls_group_client() -> None:
    """``create()`` should forward to ``begin_create_sandbox`` with merged labels."""

    from unittest.mock import AsyncMock, MagicMock

    async def _coro() -> None:
        # Build a fake group client whose ``begin_create_sandbox`` returns an
        # AsyncLROPoller-shaped object whose ``result()`` yields a fake
        # SandboxClient with the fields we serialize into state.
        fake_sandbox = MagicMock()
        fake_sandbox.sandbox_id = "sb-fake"
        fake_poller = MagicMock()
        fake_poller.result = AsyncMock(return_value=fake_sandbox)
        fake_gc = MagicMock()
        fake_gc.begin_create_sandbox = AsyncMock(return_value=fake_poller)
        fake_gc.subscription_id = "sub-x"
        fake_gc.resource_group = "rg-x"
        fake_gc.sandbox_group = "grp-x"
        fake_gc.endpoint = "https://management.westus2.azuredevcompute.io"

        client = ACASandboxesClient(fake_gc)
        # We do not start the wrapper session (no need to mock the inner
        # workspace machinery) — we just assert the SDK call shape.
        await client.create(
            options=ACASandboxesClientOptions(disk="ubuntu", labels={"tenant": "demo"}),
        )

        fake_gc.begin_create_sandbox.assert_awaited_once()
        kwargs = fake_gc.begin_create_sandbox.await_args.kwargs
        assert kwargs["disk"] == "ubuntu"
        labels = kwargs["labels"]
        # Provider defaults are always present alongside user labels.
        assert labels["scenario"] == "08-sandbox-agents"
        assert labels["framework"] == "openai"
        assert labels["tenant"] == "demo"
        assert "session-id" in labels

    asyncio.run(_coro())


def test_backend_id_is_stable() -> None:
    assert ACASandboxesClient.backend_id == "aca_sandboxes"
