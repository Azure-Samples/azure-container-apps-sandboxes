"""Typed exceptions for the ACA Sandboxes provider.

The OpenAI Agents SDK raises and catches a small family of provider-neutral
exception classes (``ExecTimeoutError``, ``ExecTransportError``,
``WorkspaceReadNotFoundError``, ``WorkspaceArchiveReadError``,
``WorkspaceArchiveWriteError``, ``ExposedPortUnavailableError``,
``SandboxError``). We remap Azure SDK exceptions to that family so callers
don't need to special-case our backend.

The SDK error constructors require *keyword* arguments (``command=``,
``path=``, ``timeout_s=``, ``message=``). Callers therefore pass the
context for the failing op alongside the original error, and this module
shapes those kwargs to fit each error class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from agents.sandbox.errors import (
    ExecTimeoutError,
    ExecTransportError,
    SandboxError,
    WorkspaceReadNotFoundError,
)

try:  # azure.core is installed transitively via azure-containerapps-sandbox
    from azure.core.exceptions import (
        AzureError,
        ClientAuthenticationError,
        HttpResponseError,
        ResourceNotFoundError,
        ServiceRequestError,
        ServiceResponseError,
    )
except ImportError:  # pragma: no cover — guard for static analysis
    AzureError = Exception  # type: ignore[assignment, misc]
    ClientAuthenticationError = Exception  # type: ignore[assignment, misc]
    HttpResponseError = Exception  # type: ignore[assignment, misc]
    ResourceNotFoundError = Exception  # type: ignore[assignment, misc]
    ServiceRequestError = Exception  # type: ignore[assignment, misc]
    ServiceResponseError = Exception  # type: ignore[assignment, misc]


def remap_exception(
    error: Exception,
    *,
    context: str | None = None,
    command: Sequence[str | Path] | None = None,
    path: str | Path | None = None,
    timeout_s: float | None = None,
) -> Exception:
    """Return the SDK-native exception that best describes *error*.

    Pass-through if *error* already inherits from
    :class:`agents.sandbox.errors.SandboxError`. The original exception is
    chained via ``__cause__`` automatically when callers use ``raise ... from``.
    """

    if isinstance(error, SandboxError):
        return error

    cmd = list(command) if command is not None else []
    ctx_dict: dict[str, object] = {"context": context} if context else {}
    msg = _format(error, context)

    def _path() -> Path:
        if path is None:
            return Path("?")
        return Path(str(path)) if not isinstance(path, Path) else path

    if isinstance(error, TimeoutError):
        return ExecTimeoutError(
            command=cmd,
            timeout_s=timeout_s,
            context=ctx_dict or None,
            cause=error,
        )

    if isinstance(error, ResourceNotFoundError):
        return WorkspaceReadNotFoundError(
            path=_path(),
            context=ctx_dict or None,
            cause=error,
        )

    if isinstance(error, ClientAuthenticationError):
        return ExecTransportError(
            command=cmd,
            context=ctx_dict or None,
            cause=error,
            message=msg,
        )

    if isinstance(error, HttpResponseError):
        status: Any = getattr(error, "status_code", None)
        if status == 404:
            return WorkspaceReadNotFoundError(
                path=_path(),
                context=ctx_dict or None,
                cause=error,
            )
        return ExecTransportError(
            command=cmd,
            context=ctx_dict or None,
            cause=error,
            message=msg,
        )

    if isinstance(error, (ServiceRequestError, ServiceResponseError, AzureError)):
        return ExecTransportError(
            command=cmd,
            context=ctx_dict or None,
            cause=error,
            message=msg,
        )

    return error


def _format(error: Exception, context: str | None) -> str:
    prefix = f"{context}: " if context else ""
    return f"{prefix}{type(error).__name__}: {error}"
