"""Shared fixtures and helpers for the samples test suite.

Two tiers live under tests/:
  - offline gate (default): fast, no Azure, meant to block merge in CI.
  - on-demand e2e (tests/e2e): real sample runs, skipped unless RUN_E2E=1.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_ls_files(*patterns: str) -> list[Path]:
    """Return tracked files matching the given pathspecs, as absolute paths.

    Using ``git ls-files`` means untracked junk (build output, local
    scratch files) is ignored, so the gate only ever looks at committed
    content.
    """
    out = subprocess.run(
        ["git", "ls-files", *patterns],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def tracked_py_files(*pathspecs: str) -> list[Path]:
    """All tracked ``*.py`` files, optionally scoped to pathspecs."""
    specs = pathspecs or ("*.py",)
    return _git_ls_files(*specs)


def tracked_md_files() -> list[Path]:
    """All tracked ``*.md`` files."""
    return _git_ls_files("*.md")


def sample_dirs(base: str) -> list[Path]:
    """Top-level sample directories under ``base`` (e.g. 'python/samples').

    Skips plain files such as README.md so callers get directories only.
    """
    root = REPO_ROOT / base
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: real Azure end-to-end run, skipped unless RUN_E2E=1",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_E2E") == "1":
        return
    skip_e2e = pytest.mark.skip(
        reason=(
            "e2e suite is opt-in: set RUN_E2E=1, run 'az login', and provision "
            "python/samples/.env (uv run python/samples/setup/setup.py) first."
        )
    )
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
