"""Local-dev runner for 10-connectors-email-triage.

Boots a sandbox **outside** an azd deployment, exactly the way the
receiver Container App boots one per webhook hit, so you can iterate
on the triage prompt or the MCP wiring without ``azd up`` / ``azd
deploy``.

Configuration comes from ``samples/.env`` plus three scenario-specific
keys:

    CONNECTOR_GATEWAY_ID            ARM ID of the Connector Gateway
    TEAMS_MCP_SERVER_CONFIG_NAME    name of the Teams MCP server config
    CONNECTOR_GATEWAY_API_KEY       gateway API key (stamped onto egress)

You can fetch the first two from ``azd env get-values`` after at
least one ``azd up`` of this scenario, and the API key from the
gateway's data-plane ``listapikey`` endpoint (the post-deploy script
runs that for you and prints the value).

Usage::

    cd python
    uv run --extra connectors run.py --email samples/sample-email.json
    uv run --extra connectors run.py --email path/to/your-email.json --dry-run

``--dry-run`` skips the actual ``copilot`` invocation and just prints
the prompt + the egress policy it would apply — useful for iterating
on the prompt template alone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

from azure.identity.aio import DefaultAzureCredential
from azure.containerapps.sandbox import EgressHeader, endpoint_for_region
from azure.containerapps.sandbox.aio import SandboxGroupClient

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

THIS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = THIS_DIR.parent / "prompts"


def _load_env() -> None:
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break
    missing = [
        n for n in (
            "ACA_SANDBOXGROUP_REGION", "AZURE_SUBSCRIPTION_ID",
            "ACA_RESOURCE_GROUP", "ACA_SANDBOX_GROUP",
        )
        if not os.environ.get(n)
    ]
    if missing:
        sys.exit(
            "error: samples/.env is missing required keys: " + ", ".join(missing) +
            "\n       Run: python python/samples/setup/setup.py"
        )


def _render_prompt(email: dict[str, Any], run_id: str) -> str:
    template = (PROMPTS_DIR / "triage.md").read_text(encoding="utf-8")
    subject = email.get("subject", "")
    sender = email.get("from", "")
    body = email.get("bodyPreview") or email.get("body", "")
    if isinstance(body, dict):
        body = body.get("content", "")
    body = str(body)[:2000]
    return (
        template
        .replace("{run_id}", run_id)
        .replace("{subject}", subject)
        .replace("{from}", str(sender))
        .replace("{body_preview}", body)
    )


async def _discover_mcp_endpoint(
    credential: DefaultAzureCredential, gateway_id: str, mcp_name: str,
) -> str:
    arm = (
        f"https://management.azure.com{gateway_id}/mcpserverconfigs/{mcp_name}"
        "?api-version=2026-05-01-preview"
    )
    token = (await credential.get_token("https://management.azure.com/.default")).token
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(arm, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
    url = data.get("properties", {}).get("mcpEndpointUrl")
    if not url:
        raise RuntimeError(f"mcpserverConfig {mcp_name!r} has no mcpEndpointUrl yet")
    return url


def _mcp_host(url: str) -> str:
    return url.split("://", 1)[1].split("/", 1)[0]


async def run(args: argparse.Namespace) -> int:
    _load_env()
    region = os.environ["ACA_SANDBOXGROUP_REGION"]
    sub = os.environ["AZURE_SUBSCRIPTION_ID"]
    rg = os.environ["ACA_RESOURCE_GROUP"]
    sg = os.environ["ACA_SANDBOX_GROUP"]

    gateway_id = os.environ.get("CONNECTOR_GATEWAY_ID", "").strip()
    mcp_name = os.environ.get("TEAMS_MCP_SERVER_CONFIG_NAME", "").strip()
    api_key = os.environ.get("CONNECTOR_GATEWAY_API_KEY", "").strip()
    if not (gateway_id and mcp_name and api_key):
        sys.exit(
            "error: set CONNECTOR_GATEWAY_ID, TEAMS_MCP_SERVER_CONFIG_NAME, "
            "and CONNECTOR_GATEWAY_API_KEY (e.g., from `azd env get-values`)."
        )

    email = json.loads(Path(args.email).read_text(encoding="utf-8"))
    run_id = uuid.uuid4().hex[:8]
    prompt = _render_prompt(email, run_id)

    print("=" * 72)
    print("LOCAL DEV — connectors-email-triage")
    print("=" * 72)
    print(f"run id        : {run_id}")
    print(f"subject       : {email.get('subject', '(none)')[:80]}")
    print(f"from          : {email.get('from', '(none)')}")
    print(f"sandbox group : {sg} ({region})")
    print(f"MCP config    : {mcp_name}")
    print()

    cred = DefaultAzureCredential()
    try:
        mcp_url = await _discover_mcp_endpoint(cred, gateway_id, mcp_name)
        host = _mcp_host(mcp_url)
        print(f"==> MCP endpoint discovered: {mcp_url}")
        print(f"    Will allow + Transform on host: {host}")
        print()

        if args.dry_run:
            print("=" * 72)
            print("PROMPT (dry-run, sandbox not booted)")
            print("=" * 72)
            print(prompt)
            return 0

        sb_client = SandboxGroupClient(
            endpoint=endpoint_for_region(region), credential=cred,
            subscription_id=sub, resource_group=rg, sandbox_group=sg,
        )
        async with sb_client:
            print("==> Booting sandbox...")
            poller = await sb_client.begin_create_sandbox(
                disk="ubuntu", cpu="2000m", memory="4096Mi",
                labels={"scenario": "connectors-email-triage", "run": run_id, "mode": "local"},
            )
            sandbox = await poller.result()
            print(f"    sandbox: {sandbox.sandbox_id}")
            try:
                print("==> Installing Copilot CLI...")
                r = await sandbox.exec(
                    "timeout 180s bash -lc 'curl -fsSL https://gh.io/copilot-install | bash'"
                )
                if r.exit_code != 0:
                    raise RuntimeError(f"copilot install failed: {(r.stderr or '')[:400]}")

                print("==> Applying egress policy (Deny + Allow + Transform)...")
                await sandbox.set_egress_default("Deny")
                await sandbox.add_egress_host_rule(host, action="Allow")
                await sandbox.add_egress_transform_rule(
                    host=host,
                    headers=[EgressHeader(operation="Set", name="X-API-Key", value=api_key)],
                    name="mcp-api-key",
                )

                print("==> Staging prompt + MCP server config...")
                mcp_json = (
                    '{\n'
                    '  "servers": {\n'
                    '    "teams": {\n'
                    '      "type": "http",\n'
                    f'      "url": "{mcp_url}"\n'
                    '    }\n'
                    '  }\n'
                    '}\n'
                )
                await sandbox.write_file("/root/.config/copilot/mcp.json", mcp_json.encode("utf-8"))
                await sandbox.write_file("/tmp/prompt.md", prompt.encode("utf-8"))

                print("==> Running copilot --allow-all-tools -p @prompt.md ...")
                r = await sandbox.exec(
                    "timeout 240s bash -lc 'copilot --allow-all-tools -p \"$(cat /tmp/prompt.md)\"'"
                )
                print(f"    exit={r.exit_code}")
                print(f"    stdout:\n{(r.stdout or '')[-1500:]}")
                if r.stderr:
                    print(f"    stderr:\n{(r.stderr or '')[-500:]}")
                return r.exit_code
            finally:
                print(f"==> Deleting sandbox {sandbox.sandbox_id}...")
                try:
                    await sandbox.delete()
                except Exception as exc:  # noqa: BLE001
                    print(f"    warning: delete failed: {exc}")
    finally:
        await cred.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local-dev runner for connectors-email-triage scenario.",
    )
    parser.add_argument(
        "--email", default=str(THIS_DIR / "samples" / "sample-email.json"),
        help="Path to an email JSON payload (default: bundled sample-email.json).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Render the prompt + print the egress plan, but don't boot a sandbox.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
