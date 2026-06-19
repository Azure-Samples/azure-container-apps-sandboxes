"""Pydantic models for the ACA Sandboxes provider.

These models register themselves with the OpenAI Agents SDK polymorphic
options/state registries via the ``type`` discriminator. Importing this module
is enough to make ``ACASandboxesClientOptions(**payload)`` and
``BaseSandboxClientOptions.parse(payload)`` round-trip cleanly.

Per-create options live on :class:`ACASandboxesClientOptions`. Per-session
identity (for resume) lives on :class:`ACASandboxesSessionState`. The transport
handle (`azure.containerapps.sandbox.aio.SandboxGroupClient`) is held on the
client, **not** on options or state — keep the contract close to the upstream
Docker provider so the demos and tests read the same way.
"""

from __future__ import annotations

from typing import Literal

from agents.sandbox.session.sandbox_client import BaseSandboxClientOptions
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_DISK = "ubuntu"
DEFAULT_POLLING_INTERVAL_SECONDS = 3.0
DEFAULT_POLLING_TIMEOUT_SECONDS = 300.0

# Stamped on every demo-created sandbox so cleanup can target only ours.
DEFAULT_LABELS: dict[str, str] = {
    "scenario": "08-sandbox-agents",
    "framework": "openai",
    "provider": "aca_sandboxes",
}


class ACASandboxVolumeMount(BaseModel):
    """Mount an existing sandbox-group volume into the sandbox.

    The volume itself must already exist on the parent sandbox group (create
    one via ``aca sandboxgroup volume create --type AzureBlob`` or the SDK
    equivalent). This struct is the per-create wiring that maps it to a
    workspace path.
    """

    volume_name: str = Field(
        ..., description="Name of the sandbox-group volume (e.g. 'shared-memory')."
    )
    mountpoint: str = Field(
        ..., description="Absolute path inside the sandbox (e.g. '/workspace/memories')."
    )
    read_only: bool | None = Field(
        default=None,
        description="Mount read-only; default (None) lets the SDK pick its default.",
    )

    model_config = ConfigDict(frozen=True)


class ACASandboxesClientOptions(BaseSandboxClientOptions):
    """Options controlling how :meth:`ACASandboxesClient.create` provisions a sandbox.

    Fields map directly onto the underlying
    :meth:`azure.containerapps.sandbox.aio.SandboxGroupClient.begin_create_sandbox`
    parameters with two additions: ``labels`` are merged with
    :data:`DEFAULT_LABELS` for safe cleanup, and ``polling_*`` control how long
    we wait for sandbox lifecycle long-running operations.
    """

    type: Literal["aca_sandboxes"] = "aca_sandboxes"

    disk: str = Field(
        default=DEFAULT_DISK,
        description="Public disk image name (see: aca sandboxgroup disk list-public).",
    )
    disk_id: str | None = Field(
        default=None,
        description="Private disk image ID (overrides ``disk`` when set).",
    )
    snapshot_id: str | None = Field(
        default=None,
        description="Snapshot ID to restore from. When set, ``disk``/``disk_id`` are ignored.",
    )
    cpu: str | None = Field(default=None, description="CPU request (e.g. '1000m').")
    memory: str | None = Field(default=None, description="Memory request (e.g. '2048Mi').")
    labels: dict[str, str] | None = Field(
        default=None,
        description=(
            "User labels merged with the provider's default labels. The demo's "
            "default labels (scenario, framework, provider) are always present "
            "so cleanup can never accidentally delete unrelated sandboxes."
        ),
    )
    exposed_ports: tuple[int, ...] = Field(
        default_factory=tuple,
        description="TCP ports to publish on create.",
    )
    volume_mounts: tuple[ACASandboxVolumeMount, ...] = Field(
        default_factory=tuple,
        description=(
            "Sandbox-group volumes to mount into this sandbox at create time. "
            "Use Azure Blob volumes for cross-session/cross-sandbox memory."
        ),
    )
    polling_interval_seconds: float = Field(
        default=DEFAULT_POLLING_INTERVAL_SECONDS,
        gt=0.0,
        description="Poll interval for begin_create_sandbox / begin_delete.",
    )
    polling_timeout_seconds: float = Field(
        default=DEFAULT_POLLING_TIMEOUT_SECONDS,
        gt=0.0,
        description="Polling deadline for sandbox lifecycle operations.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    def merged_labels(self) -> dict[str, str]:
        """Return the final ``labels`` dict applied to the sandbox.

        User-supplied labels override defaults on key collision so callers can
        opt out of (or relabel) a default — but the demo never needs to.
        """

        merged: dict[str, str] = dict(DEFAULT_LABELS)
        if self.labels:
            merged.update(self.labels)
        return merged


class ACASandboxesSessionState(SandboxSessionState):
    """JSON-serialisable identity of an ACA sandbox.

    Use :meth:`ACASandboxesClient.resume` (or
    :func:`agents.sandbox.session.sandbox_session_state.SandboxSessionState.parse`)
    to re-attach to an existing sandbox by its ``sandbox_id``.
    """

    type: Literal["aca_sandboxes"] = "aca_sandboxes"

    sandbox_id: str
    """ACA sandbox UUID; the value returned by ``Sandbox.id`` from list_sandboxes."""

    sandbox_group: str
    """Name of the parent sandbox group."""

    resource_group: str
    """Azure resource group that owns the sandbox group."""

    subscription_id: str
    """Azure subscription that owns the resource group."""

    region: str
    """Azure region of the sandbox group (used to compute the data-plane endpoint)."""
