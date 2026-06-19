"""ACA Sandboxes provider for the OpenAI Agents SDK.

This package implements :class:`agents.sandbox.session.sandbox_client.BaseSandboxClient`
for `Azure Container Apps Sandboxes <https://github.com/microsoft/azure-container-apps>`_,
letting :class:`agents.sandbox.SandboxAgent` run with the same ergonomics as the
built-in Docker and Unix providers.

It is published as the sibling top-level module ``agents_aca_sandboxes`` rather
than living under ``agents.extensions.sandbox.*`` because that namespace is
owned by the upstream Agents SDK distribution and is not extensible by
third-party packages.

Public API
----------
- :class:`ACASandboxesClient` — the provider client; constructed with an
  ``azure.containerapps.sandbox.aio.SandboxGroupClient`` transport handle.
- :class:`ACASandboxesClientOptions` — per-create options (disk, labels,
  exposed ports, polling).
- :class:`ACASandboxesSessionState` — JSON-serialisable session identity used
  by :meth:`ACASandboxesClient.resume`.
- :class:`ACASandboxesSession` — the session class exposed by the agent
  runtime; you generally interact with the wrapping :class:`SandboxSession`
  returned from :meth:`ACASandboxesClient.create`.
"""

from __future__ import annotations

from ._config import ACASandboxesEnvConfig, load as load_config
from ._version import __version__
from .models import (
    ACASandboxesClientOptions,
    ACASandboxesSessionState,
    ACASandboxVolumeMount,
)
from .sandbox import ACASandboxesClient, ACASandboxesSession

__all__ = [
    "ACASandboxesClient",
    "ACASandboxesClientOptions",
    "ACASandboxesEnvConfig",
    "ACASandboxesSession",
    "ACASandboxesSessionState",
    "ACASandboxVolumeMount",
    "load_config",
    "__version__",
]
