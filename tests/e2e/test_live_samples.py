"""On-demand e2e: real sample executions against Azure.

Skipped by default. To run:
    az login
    uv run python/samples/setup/setup.py        # provisions python/samples/.env
    RUN_E2E=1 uv run --group test pytest tests/e2e -m e2e

Most tests shell out to ``uv run <sample script>`` and assert the process
exits 0 plus a couple of stable stdout markers. The egress test drives
the SDK in-process. All sandboxes the samples create self-clean in their
own finally blocks, so a green run leaves nothing billable behind.

Coverage tiers:
  - LIGHT (only setup.py needed): 01-webapps, 02-coding-agents,
    04-swarms 01 and 02, 05-data-processing, 06-developer-workflows,
    plus the focused egress test. These run with base dependencies and
    no extra credentials.
  - PROVISIONED (skip unless wired): the connectors test needs a
    standing Connector Gateway (CONNECTOR_GATEWAY_ID,
    TEAMS_MCP_SERVER_CONFIG_NAME, CONNECTOR_GATEWAY_API_KEY). It skips
    cleanly anywhere those are absent.

Not covered here (and why): 03-code-interpreter and 08-sandbox-agents
need an Azure OpenAI deployment (AZURE_OPENAI_ENDPOINT/DEPLOYMENT);
11-connectors-document-automation is an azd-deployed listener service,
not a one-shot host script; 09-mcp-hosting/dab-sql-devtunnel needs an
interactive devtunnel device-code login. 09-mcp-hosting/excalidraw is
runnable but clones and npm-builds inside the sandbox, too slow and
network-dependent for the default set.
"""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

pytestmark = pytest.mark.e2e

ENV_FILE = REPO_ROOT / "python" / "samples" / ".env"
TIMEOUT = 600

_GATEWAY_VARS = (
    "CONNECTOR_GATEWAY_ID",
    "TEAMS_MCP_SERVER_CONFIG_NAME",
    "CONNECTOR_GATEWAY_API_KEY",
)


def _require_env():
    if not ENV_FILE.exists():
        pytest.skip(
            f"missing {ENV_FILE}; run 'uv run python/samples/setup/setup.py' first"
        )


def _load_samples_env() -> None:
    """Read python/samples/.env into the process env for in-process tests."""
    _require_env()
    for line in ENV_FILE.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _run_sample(rel_path: str, *, extra: str | None = None) -> subprocess.CompletedProcess:
    _require_env()
    script = REPO_ROOT / rel_path
    assert script.exists(), f"sample script not found: {rel_path}"
    cmd = ["uv", "run"]
    if extra:
        cmd += ["--extra", extra]
    cmd.append(str(script))
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=subprocess.DEVNULL,
    )


# ---- LIGHT tier: boot a sandbox, run, self-clean -------------------------


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


def test_04_swarm_sandbox_inception():
    result = _run_sample(
        "python/samples/04-swarms/01-sandbox-inception/python/swarm.py"
    )
    assert result.returncode == 0, result.stderr
    # The host self-grants Data Owner on the fresh orchestrator group, then
    # the orchestrator spawns workers and aggregates a pi estimate.
    assert "Granting" in result.stdout and "orchestrator group" in result.stdout
    assert "Aggregating across" in result.stdout
    assert "==> Done." in result.stdout


def test_04_swarm_shared_blob_memory():
    result = _run_sample(
        "python/samples/04-swarms/02-shared-blob-memory/python/swarm.py"
    )
    assert result.returncode == 0, result.stderr
    assert "Granting" in result.stdout and "orchestrator group" in result.stdout
    assert "Aggregating across" in result.stdout
    assert "==> Done." in result.stdout


def test_05_data_processing():
    result = _run_sample(
        "python/samples/05-data-processing/python/pipeline.py"
    )
    assert result.returncode == 0, result.stderr
    assert "users by event count" in result.stdout
    assert "==> Done." in result.stdout


def test_06_developer_workflows():
    result = _run_sample(
        "python/samples/06-developer-workflows/python/ci.py"
    )
    # ci.py builds three mock PRs and one fails its tests on purpose (the
    # whole point of the demo), so it exits 2. A clean run is 0; a run
    # where the harness itself broke is anything else.
    assert result.returncode in (0, 2), result.stderr
    assert "PRs passed" in result.stdout


# ---- Focused egress test: guards the A3 trafficInspection fix -------------


def test_egress_deny_transform_full_inspection():
    """Boot one sandbox, apply Deny default + Full inspection + a Transform
    rule, then read the policy back and assert it stuck. This directly
    regression-guards the A3 fix (Transform rules need trafficInspection:
    Full to fire)."""
    _load_samples_env()
    from azure.identity import DefaultAzureCredential
    from azure.containerapps.sandbox import (
        EgressHeader,
        SandboxGroupClient,
        endpoint_for_region,
    )

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(os.environ["ACA_SANDBOXGROUP_REGION"]),
        credential,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group=os.environ["ACA_RESOURCE_GROUP"],
        sandbox_group=os.environ["ACA_SANDBOX_GROUP"],
    )
    sandbox = None
    try:
        sandbox = client.begin_create_sandbox(disk="node-22").result()
        sandbox.set_egress_default("Deny")
        policy = sandbox.get_egress_policy()
        policy.traffic_inspection = "Full"
        sandbox.set_egress_policy(policy)
        sandbox.add_egress_transform_rule(
            host="api.github.com",
            headers=[EgressHeader(operation="Set", name="X-API-Key", value="test-value")],
            name="e2e-egress-probe",
        )

        got = sandbox.get_egress_policy()
        assert got.default_action == "Deny", got.default_action
        assert got.traffic_inspection == "Full", got.traffic_inspection
        assert any(
            getattr(r.action, "type", None) == "Transform" for r in got.rules
        ), f"Transform rule missing from read-back policy: {got.rules}"
    finally:
        if sandbox is not None:
            try:
                sandbox.delete()
            except Exception as exc:  # noqa: BLE001
                print(f"warning: sandbox delete failed: {exc}")
        client.close()
        credential.close()


# ---- PROVISIONED tier: skip unless a Connector Gateway is wired -----------


def _require_connector_gateway():
    missing = [v for v in _GATEWAY_VARS if not os.environ.get(v)]
    if missing:
        pytest.skip(
            "connector gateway not provisioned; set "
            + ", ".join(_GATEWAY_VARS)
            + " (from 'azd env get-values') to run this. Missing: "
            + ", ".join(missing)
        )


def test_10_connectors_email_triage():
    _require_connector_gateway()
    result = _run_sample(
        "python/samples/10-connectors-email-triage/python/run.py",
        extra="connectors",
    )
    assert result.returncode == 0, result.stderr
    # Proves the live gateway integration worked: the runner resolved the
    # MCP endpoint off the standing Connector Gateway before booting.
    assert "MCP endpoint discovered" in result.stdout
