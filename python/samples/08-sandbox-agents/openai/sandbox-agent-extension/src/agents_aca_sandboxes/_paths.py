"""Helpers for the small set of POSIX path operations the provider needs.

The OpenAI Agents SDK passes ``pathlib.Path`` instances that may have host
flavour (PosixPath on Linux/macOS, WindowsPath on Windows). The ACA sandbox
filesystem is POSIX, so we coerce to forward-slash strings before sending and
parse forward-slash strings on the way back.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

WORKSPACE_ROOT = "/workspace"


def to_posix(path: Path | str) -> str:
    """Return *path* as an absolute POSIX string anchored at ``/`` or ``/workspace``."""

    if isinstance(path, Path):
        text = path.as_posix()
    else:
        text = str(path).replace("\\", "/")

    if not text:
        return WORKSPACE_ROOT

    if text.startswith("/"):
        return text

    return f"{WORKSPACE_ROOT}/{text.lstrip('/')}"


def posix_path(path: Path | str) -> PurePosixPath:
    """Return *path* as a :class:`PurePosixPath` with workspace anchoring."""

    return PurePosixPath(to_posix(path))


def is_within(child: str, parent: str) -> bool:
    """Return ``True`` if *child* is the same as or nested under *parent*."""

    child_parts = PurePosixPath(child).parts
    parent_parts = PurePosixPath(parent).parts
    if len(child_parts) < len(parent_parts):
        return False
    return child_parts[: len(parent_parts)] == parent_parts
