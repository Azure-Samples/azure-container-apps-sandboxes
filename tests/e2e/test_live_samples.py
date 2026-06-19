"""On-demand e2e: real sample executions against Azure.

Skipped by default. To run:
    az login
    uv run python/samples/setup/setup.py        # provisions python/samples/.env
    RUN_E2E=1 uv run --group test pytest tests/e2e

Each test shells out to ``uv run <sample script>`` and asserts the
process exits 0 plus a couple of stable stdout markers.

Adding more samples later (kept out by default because they need extra
setup):
  - 04-swarms: needs Data Owner at the resource-group scope (or a
    per-orchestrator-group role grant) before the orchestrator can spawn
    nested sandboxes.
  - 10/11 connectors: need an azd-deployed gateway in front of the
    connector before the sample can call it.
Follow the same subprocess + marker pattern once that setup exists.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

pytestmark = pytest.mark.e2e

ENV_FILE = REPO_ROOT / "python" / "samples" / ".env"
TIMEOUT = 300


def _require_env():
    if not ENV_FILE.exists():
        pytest.skip(
            f"missing {ENV_FILE}; run 'uv run python/samples/setup/setup.py' first"
        )


def _run_sample(rel_path: str) -> subprocess.CompletedProcess:
    _require_env()
    script = REPO_ROOT / rel_path
    assert script.exists(), f"sample script not found: {rel_path}"
    return subprocess.run(
        ["uv", "run", str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=subprocess.DEVNULL,
    )


def test_01_simple_anonymous_webapp():
    result = _run_sample(
        "python/samples/01-webapps/simple-anonymous/python/run.py"
    )
    assert result.returncode == 0, result.stderr
    assert "adcproxy.io" in result.stdout
    assert "All endpoint shape assertions passed" in result.stdout


def test_02_gh_copilot_cli():
    result = _run_sample(
        "python/samples/02-coding-agents/gh-copilot-cli/python/copilot.py"
    )
    assert result.returncode == 0, result.stderr
    assert "Sandbox is ready" in result.stdout
    assert "portal.azure.com" in result.stdout or "sandboxes.azure.com" in result.stdout
