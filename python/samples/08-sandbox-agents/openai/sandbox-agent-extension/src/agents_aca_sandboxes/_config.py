"""Configuration helper for the ACA Sandboxes provider.

The provider itself does not own credentials or transport — that lives in the
caller (or sample). This module gives the samples a single place to look up
env vars, walk up to ``samples/.env``, and fall back to ``aca config show`` so
the demos work out of the box on a developer machine.

Usage::

    from agents_aca_sandboxes import config
    cfg = config.load()
    cred = DefaultAzureCredential()
    async with SandboxGroupClient(
        endpoint=cfg.endpoint,
        credential=cred,
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        sandbox_group=cfg.sandbox_group,
    ) as gc:
        ...
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from azure.containerapps.sandbox import endpoint_for_region


def _walk_up_for(filename: str, start: Path | None = None, *, levels: int = 8) -> Path | None:
    """Walk from *start* (default: cwd) upwards looking for ``samples/<filename>``."""

    here = (start or Path.cwd()).resolve()
    for _ in range(levels):
        candidate = here / "samples" / filename
        if candidate.is_file():
            return candidate
        if here.parent == here:
            return None
        here = here.parent
    return None


def _hydrate_env_from_file(path: Path) -> None:
    """Populate ``os.environ`` from *path* without overwriting existing keys."""

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _aca_config_show() -> dict[str, str]:
    """Best-effort fallback to ``aca config show`` for ACA fields.

    Quiet failure: if ``aca`` is not on PATH, returns ``{}``.
    """

    try:
        result = subprocess.run(  # noqa: S603 — argv list, fixed binary
            ["aca", "config", "show", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}
    return {str(k): str(v) for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class ACASandboxesEnvConfig:
    """Resolved ACA sandbox group configuration for the samples."""

    subscription_id: str
    resource_group: str
    sandbox_group: str
    region: str

    @property
    def endpoint(self) -> str:
        """Data-plane endpoint computed from :attr:`region`."""

        return endpoint_for_region(self.region)


_ENV_FILENAME = ".env"
_ALIASES: dict[str, tuple[str, ...]] = {
    # First name wins; second/third are accepted for back-compat with the repo's
    # mixed conventions (samples/.env.example uses both forms).
    "subscription_id": ("ACA_SUBSCRIPTION", "AZURE_SUBSCRIPTION_ID"),
    "resource_group": ("ACA_RESOURCE_GROUP", "AZURE_RESOURCE_GROUP"),
    "sandbox_group": ("ACA_SANDBOX_GROUP", "ACA_SANDBOXGROUP"),
    "region": ("ACA_REGION", "ACA_SANDBOXGROUP_REGION", "AZURE_REGION"),
}
_ACA_CONFIG_KEYS: dict[str, str] = {
    "subscription_id": "subscription",
    "resource_group": "resource_group",
    "sandbox_group": "sandbox_group",
    "region": "region",
}


def load(*, search_start: Path | None = None) -> ACASandboxesEnvConfig:
    """Resolve a fully-populated config from env vars, ``samples/.env``, and aca CLI.

    Resolution order, per field:

    1. ``os.environ`` value under any alias listed in :data:`_ALIASES`.
    2. ``samples/.env`` walked up from *search_start* (default: cwd).
    3. ``aca config show -o json``.

    Raises :class:`RuntimeError` listing every missing field on the first
    unresolvable lookup so the sample can print one helpful error.
    """

    env_path = _walk_up_for(_ENV_FILENAME, start=search_start)
    if env_path is not None:
        _hydrate_env_from_file(env_path)

    aca_payload: dict[str, str] | None = None

    def _resolve(field: str) -> str | None:
        for alias in _ALIASES[field]:
            value = os.environ.get(alias)
            if value:
                return value
        return None

    resolved: dict[str, str] = {}
    for field in _ALIASES:
        value = _resolve(field)
        if value is None:
            if aca_payload is None:
                aca_payload = _aca_config_show()
            cli_key = _ACA_CONFIG_KEYS[field]
            value = aca_payload.get(cli_key)
        if value:
            resolved[field] = value

    missing = [field for field in _ALIASES if field not in resolved]
    if missing:
        env_hint = (
            f" (looked in {env_path})" if env_path else " (no samples/.env found)"
        )
        names = ", ".join(_ALIASES[f][0] for f in missing)
        raise RuntimeError(
            f"ACA sandbox config missing required field(s): {names}{env_hint}. "
            "Set them in samples/.env, in the process env, or run `aca config set`."
        )

    return ACASandboxesEnvConfig(**resolved)


__all__ = ("ACASandboxesEnvConfig", "load")
