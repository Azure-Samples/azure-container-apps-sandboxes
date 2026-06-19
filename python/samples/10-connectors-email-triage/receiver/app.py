"""Receiver — ACA app that boots a sandbox per inbound email event.

Flow per webhook request:

  1. Connector Gateway POSTs the Office 365 "When a new email arrives"
     trigger payload to ``/webhook``.
  2. We acknowledge with ``200`` immediately so the gateway doesn't
     retry, then process each email in a background task.
  3. For each email: boot a sandbox in the configured sandbox group,
     install Copilot CLI, install a deny-default egress policy with
     an ``X-API-Key`` Transform rule on the Connector Gateway MCP
     host, drop the triage prompt as a file, run
     ``copilot --allow-all-tools -p @prompt.md`` so Copilot reads the
     email + Teams MCP URL and posts a triage card. Sandbox is then
     deleted.

The Connector Gateway API key is fetched from an environment variable
(``CONNECTOR_GATEWAY_API_KEY``) populated at deploy time by the
post-deploy script (via the gateway's ``listapikey`` data-plane API).
The key never enters the sandbox — it lives on the receiver and is
stamped onto outbound requests by the egress proxy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from azure.identity.aio import DefaultAzureCredential
from azure.containerapps.sandbox import EgressHeader, SandboxVolume, endpoint_for_region
from azure.containerapps.sandbox.aio import SandboxGroupClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("receiver")

# ---- Configuration (populated by Bicep via env) ---------------------------

ACA_SANDBOX_GROUP_ID = os.environ["ACA_SANDBOX_GROUP_ID"]
ACA_SANDBOX_GROUP_REGION = os.environ["ACA_SANDBOX_GROUP_REGION"]
CONNECTOR_GATEWAY_ID = os.environ["CONNECTOR_GATEWAY_ID"]
TEAMS_MCP_SERVER_CONFIG_NAME = os.environ["TEAMS_MCP_SERVER_CONFIG_NAME"]
# Populated by post-deploy. While empty, the receiver still serves
# health checks but rejects /webhook with 503 until the key arrives —
# this is how we avoid running with a broken egress Transform rule.
CONNECTOR_GATEWAY_API_KEY = os.environ.get("CONNECTOR_GATEWAY_API_KEY", "").strip()
# Required only when bypassing auth (local dev). In azd-deploy mode,
# the Connector Gateway uses MI + Easy Auth at the ACA edge.
WEBHOOK_SHARED_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "").strip()
# GitHub PAT used to authenticate Copilot CLI to GitHub Models (the
# LLM backend Copilot itself calls). Populated by post-deploy from the
# `azd env set GITHUB_PAT ...` value. Like CONNECTOR_GATEWAY_API_KEY,
# this never enters the sandbox — the egress proxy stamps it onto
# outbound requests to api.github.com and the two githubcopilot.com
# hosts at the sandbox boundary.
GITHUB_PAT = os.environ.get("GITHUB_PAT", "").strip()
# Optional pre-pinned Teams target. When set, included in the prompt
# so Copilot doesn't have to ask. When unset, the model is told to
# look up the channel from its tools — fine for demo but unreliable.
TEAMS_TEAM_ID = os.environ.get("TEAMS_TEAM_ID", "").strip()
TEAMS_CHANNEL_ID = os.environ.get("TEAMS_CHANNEL_ID", "").strip()

# GitHub host families Copilot CLI talks to that need an Authorization
# header. Mirrors scenarios/02-coding-agents/gh-copilot-cli — *not*
# adding explicit Allow rules for these hosts, because an Allow would
# short-circuit the Transform and let the request out without the PAT.
_GITHUB_TRANSFORM_HOSTS: tuple[tuple[str, str, str], ...] = (
    ("api.github.com",                         "token",  "github-api-auth"),
    ("api.enterprise.githubcopilot.com",       "Bearer", "copilot-enterprise-auth"),
    ("telemetry.enterprise.githubcopilot.com", "Bearer", "copilot-telemetry-auth"),
)

# Computed.
def _gateway_host_from_id(gateway_id: str) -> str:
    # /subscriptions/.../resourceGroups/.../providers/Microsoft.Web/connectorGateways/<name>
    parts = gateway_id.strip("/").split("/")
    return parts[-1] if parts else "gateway"


def _mcp_endpoint_url() -> str:
    """Construct the MCP runtime endpoint URL the sandbox calls.

    The Connector Gateway publishes the runtime endpoint at::

        https://{host}/api/connectorGateways/{gatewayId}/mcpserverconfigs/{name}/mcp

    where `{host}` is the regional logic-apps API hub host (assigned
    by the platform at gateway create time). We discover it once at
    startup by hitting the ARM GET on the mcpserverConfig and reading
    `properties.mcpEndpointUrl`. Cached for the receiver lifetime.
    """
    return _MCP_ENDPOINT_URL_CACHE


_MCP_ENDPOINT_URL_CACHE: str = ""


async def _discover_mcp_endpoint(credential: DefaultAzureCredential) -> str:
    """ARM GET on the McpServerConfig — returns properties.mcpEndpointUrl."""
    arm = f"https://management.azure.com{CONNECTOR_GATEWAY_ID}/mcpserverconfigs/{TEAMS_MCP_SERVER_CONFIG_NAME}?api-version=2026-05-01-preview"
    token = (await credential.get_token("https://management.azure.com/.default")).token
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(arm, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
    url = data.get("properties", {}).get("mcpEndpointUrl")
    if not url:
        raise RuntimeError(f"mcpserverConfig {TEAMS_MCP_SERVER_CONFIG_NAME!r} has no mcpEndpointUrl yet")
    return url


# ---- FastAPI app -----------------------------------------------------------

app = FastAPI(title="sandboxes-connectors-email-triage receiver")

# Long-lived async clients per-process.
_credential: DefaultAzureCredential | None = None
_sandbox_client: SandboxGroupClient | None = None
# Track in-flight processing tasks so we can drain on shutdown.
_inflight: set[asyncio.Task[Any]] = set()


@app.on_event("startup")
async def _startup() -> None:
    global _credential, _sandbox_client, _MCP_ENDPOINT_URL_CACHE
    _credential = DefaultAzureCredential()
    # Parse the sandbox-group ARM ID.
    parts = ACA_SANDBOX_GROUP_ID.strip("/").split("/")
    # /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/sandboxGroups/<name>
    sub, rg, group = parts[1], parts[3], parts[-1]
    _sandbox_client = SandboxGroupClient(
        endpoint=endpoint_for_region(ACA_SANDBOX_GROUP_REGION),
        credential=_credential,
        subscription_id=sub,
        resource_group=rg,
        sandbox_group=group,
    )
    try:
        _MCP_ENDPOINT_URL_CACHE = await _discover_mcp_endpoint(_credential)
        log.info("mcp endpoint discovered: %s", _MCP_ENDPOINT_URL_CACHE)
    except Exception as exc:  # noqa: BLE001
        log.error("could not discover MCP endpoint at startup: %s", exc)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _inflight:
        log.info("draining %d in-flight task(s)...", len(_inflight))
        await asyncio.gather(*_inflight, return_exceptions=True)
    if _sandbox_client is not None:
        await _sandbox_client.close()
    if _credential is not None:
        await _credential.close()


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "sandboxes-connectors-email-triage",
        "ready": bool(_MCP_ENDPOINT_URL_CACHE),
        "has_api_key": bool(CONNECTOR_GATEWAY_API_KEY),
        "inflight": len(_inflight),
    }


@app.get("/healthz")
async def healthz() -> Response:
    if not _MCP_ENDPOINT_URL_CACHE:
        return Response(content="mcp endpoint not yet discovered", status_code=503)
    return Response(content="ok", media_type="text/plain")


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    # Auth — system-key style for the preview/dev shape. Production
    # should put App Service built-in auth in front of this endpoint
    # and validate an Entra token issued by the gateway's MI; the
    # comment block in the Bicep covers the upgrade path.
    if WEBHOOK_SHARED_SECRET:
        provided = request.headers.get("x-connector-secret", "")
        if provided != WEBHOOK_SHARED_SECRET:
            raise HTTPException(status_code=401, detail="bad webhook secret")

    if not CONNECTOR_GATEWAY_API_KEY:
        # The gateway API key is what we'd stamp on outbound MCP calls.
        # Without it, the egress Transform rule is broken; fail loudly
        # rather than letting Copilot run and get a 401.
        raise HTTPException(status_code=503, detail="connector gateway API key not yet configured")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    # Office 365 V3 batch trigger payload looks like
    #   {"body": {"value": [ {...email...}, ... ]}}
    emails = (payload.get("body") or {}).get("value") or [payload]
    log.info("webhook received %d email(s)", len(emails))

    for email in emails:
        t = asyncio.create_task(_process_one(email))
        _inflight.add(t)
        t.add_done_callback(_inflight.discard)

    return {"accepted": len(emails)}


# ---- Per-email pipeline ---------------------------------------------------

# Static parts of the egress policy we apply to every sandbox.
_MCP_HOST_PLACEHOLDER = None  # filled in lazily after MCP URL is known


def _mcp_host() -> str:
    url = _MCP_ENDPOINT_URL_CACHE
    if not url:
        raise RuntimeError("MCP endpoint not yet discovered")
    # Strip scheme to get "<host>[:port]"
    return url.split("://", 1)[1].split("/", 1)[0]


async def _process_one(email: dict[str, Any]) -> None:
    if _sandbox_client is None:
        log.error("sandbox client not initialised")
        return

    run_id = uuid.uuid4().hex[:8]
    subject = (email.get("subject") or "").strip() or "(no subject)"
    sender = (email.get("from") or "(unknown)")
    log.info("[%s] processing email subject=%r from=%r", run_id, subject[:80], sender)

    labels = {
        "scenario": "connectors-email-triage",
        "run": run_id,
    }
    sandbox = None
    try:
        poller = await _sandbox_client.begin_create_sandbox(
            disk="ubuntu", cpu="2000m", memory="4096Mi", labels=labels,
        )
        sandbox = await poller.result()
        log.info("[%s] sandbox created id=%s", run_id, sandbox.sandbox_id)

        await _wait_exec(sandbox)

        # Install Copilot CLI before the egress lockdown — installer
        # hits gh.io etc., which the Deny default would block.
        await _install_copilot(sandbox, run_id)

        # Lock egress: deny default, allow the MCP host + GitHub Copilot
        # backplane Copilot CLI needs, then Transform rule that injects
        # X-API-Key on every outbound request to the MCP host.
        await _apply_egress_policy(sandbox)

        # Drop the prompt + MCP server config inside the sandbox.
        await _stage_prompt(sandbox, email, run_id)

        # Run Copilot non-interactively against the staged prompt.
        await _run_copilot(sandbox, run_id)

        log.info("[%s] done", run_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("[%s] processing failed: %s", run_id, exc)
    finally:
        if sandbox is not None:
            try:
                await sandbox.delete()
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] cleanup delete failed: %s", run_id, exc)


async def _wait_exec(sandbox, *, timeout_s: float = 30.0) -> None:
    """Poll sandbox.exec('true') until it returns 0 — equivalent to
    waiting for the per-VM runtime to be reachable."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        r = await sandbox.exec("true")
        if r.exit_code == 0:
            return
        await asyncio.sleep(1.5)
    raise RuntimeError("sandbox exec did not become ready in time")


async def _install_copilot(sandbox, run_id: str) -> None:
    log.info("[%s] installing Copilot CLI...", run_id)
    r = await sandbox.exec(
        "timeout 180s bash -lc 'curl -fsSL https://gh.io/copilot-install | bash'"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"copilot install failed: exit={r.exit_code} stderr={(r.stderr or '')[:200]!r}"
        )


async def _apply_egress_policy(sandbox) -> None:
    mcp_host = _mcp_host()
    await sandbox.set_egress_default("Deny")
    # MCP host Transform implicitly allows the request through AND
    # stamps the gateway API key. Don't ALSO add a host-allow rule for
    # the MCP host — a host-allow + Transform on the same host
    # short-circuits the Transform.
    await sandbox.add_egress_transform_rule(
        host=mcp_host,
        headers=[EgressHeader(
            operation="Set",
            name="X-API-Key",
            value=CONNECTOR_GATEWAY_API_KEY,
        )],
        name="mcp-api-key",
    )
    # GitHub PAT injection — Copilot CLI needs to authenticate to
    # GitHub Models (its LLM backend) and the GitHub auth/API host.
    # If GITHUB_PAT isn't set the egress lockdown still applies but
    # copilot will error 'No authentication information found' on
    # first run; we log a clear warning so the next maintainer can
    # see it in the receiver logs.
    if GITHUB_PAT:
        for host, scheme, name in _GITHUB_TRANSFORM_HOSTS:
            await sandbox.add_egress_transform_rule(
                host=host,
                headers=[EgressHeader(
                    operation="Set",
                    name="Authorization",
                    value=f"{scheme} {GITHUB_PAT}",
                )],
                name=name,
            )
    else:
        log.warning(
            "GITHUB_PAT is not set — Copilot CLI will fail to authenticate "
            "with GitHub Models. Set it via `azd env set GITHUB_PAT <token>` "
            "and re-run azd hooks run postdeploy."
        )


async def _stage_prompt(sandbox, email: dict[str, Any], run_id: str) -> None:
    """Write the triage prompt + register the Teams MCP server.

    Copilot CLI v1.x reads MCP server config from `~/.copilot/mcp-config.json`
    (user-level) and `./.mcp.json` (workspace-level), per
    `copilot mcp --help`. We write the user-level file so the same
    config is in place no matter what cwd Copilot ends up in.
    """
    mcp_url = _MCP_ENDPOINT_URL_CACHE
    # Copilot CLI mcp-config.json shape: {"mcpServers": {"<name>": {<type-specific config>}}}
    # For remote HTTP MCP servers, the type-specific config is
    # {"type": "http", "url": "..."}. The egress proxy adds X-API-Key
    # on the way out so we don't include it here.
    mcp_json = (
        '{\n'
        '  "mcpServers": {\n'
        '    "teams": {\n'
        '      "type": "http",\n'
        f'      "url": "{mcp_url}"\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    await sandbox.exec("mkdir -p /root/.copilot")
    await sandbox.write_file("/root/.copilot/mcp-config.json", mcp_json.encode("utf-8"))

    # Triage prompt — small, deterministic.  See prompts/triage.md for
    # the canonical source.
    prompt = _render_prompt(email, run_id)
    await sandbox.write_file("/tmp/prompt.md", prompt.encode("utf-8"))


def _render_prompt(email: dict[str, Any], run_id: str) -> str:
    subject = email.get("subject", "")
    sender = email.get("from", "")
    body_preview = email.get("bodyPreview") or email.get("body", "")
    if isinstance(body_preview, dict):  # Graph-style {contentType, content}
        body_preview = body_preview.get("content", "")
    teams_target = ""
    if TEAMS_TEAM_ID and TEAMS_CHANNEL_ID:
        teams_target = (
            "\n\nWhen posting to Teams, call the `teams` MCP server's "
            "`SendMessageToChannel` tool with these exact parameters:\n"
            f"  teamId:    {TEAMS_TEAM_ID}\n"
            f"  channelId: {TEAMS_CHANNEL_ID}\n"
            "  content:   <your triage card text>\n"
            "Do not call ListTeams or ListChannels — the IDs above are "
            "already correct. Use plain text content (no HTML). Do not "
            "invent any other recipients."
        )
    return (
        f"You are a triage assistant. A new email just arrived.\n\n"
        f"Run ID: {run_id}\n"
        f"Subject: {subject}\n"
        f"From: {sender}\n\n"
        f"Body preview:\n{body_preview[:2000]}\n\n"
        f"Classify this email as 'important' or 'normal'. If important, "
        f"post a short triage card (3-5 lines: subject, sender, one-sentence "
        f"reason, run id footer) to Teams using the `teams` MCP server's "
        f"`SendMessageToChannel` tool. If normal, print `verdict=normal` and "
        f"do nothing else."
        f"{teams_target}"
    )


async def _run_copilot(sandbox, run_id: str) -> None:
    log.info("[%s] running copilot...", run_id)
    # Diagnostic — show copilot version + a quick auth status check
    # before the real run, so when something's wrong we can tell whether
    # it's an auth issue vs a network issue vs the prompt itself.
    v = await sandbox.exec("timeout 10s bash -lc 'copilot --version 2>&1 || true'")
    log.info("[%s] copilot --version: %s", run_id, (v.stdout or "").strip()[:300])

    # Confirm Copilot can see our MCP server registration before we
    # spend tokens on a real run. Cheap and self-documenting.
    m = await sandbox.exec("timeout 10s bash -lc 'copilot mcp list 2>&1 || true'")
    log.info("[%s] copilot mcp list:\n%s", run_id, (m.stdout or "").strip()[:600])

    # TRADE-OFF: Copilot CLI v1 errors immediately if no credential is
    # present in its env (COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN)
    # — it does NOT attempt a network call first. That means the egress
    # proxy's Transform rule on api.github.com can't intervene to inject
    # the PAT, because Copilot never reaches the network. For
    # non-interactive use we have to put the PAT in the sandbox env
    # too. The egress-proxy Transform rule still fires on outbound
    # requests as defense-in-depth, but the sandbox sees the PAT.
    #
    # If you can't accept that trade-off, this scenario isn't the right
    # fit — switch to scenario 11 (Python tool-calling against AOAI via
    # the egress proxy, no PAT in the sandbox).
    pat_env = ""
    if GITHUB_PAT:
        pat_env = f"COPILOT_GITHUB_TOKEN={GITHUB_PAT} "

    r = await sandbox.exec(
        f"timeout 240s bash -lc '{pat_env}copilot --allow-all-tools -p \"$(cat /tmp/prompt.md)\" 2>&1'"
    )
    # Don't truncate — when Copilot fails its message includes the full
    # error contract that tells us what to fix next (auth, network, etc.).
    log.info(
        "[%s] copilot exit=%d\nstdout:\n%s\n[--- end stdout ---]",
        run_id, r.exit_code,
        (r.stdout or ""),
    )
    if r.exit_code != 0:
        raise RuntimeError(f"copilot run failed exit={r.exit_code}")


# Local dev entrypoint: `uvicorn app:app --reload --port 8080`
if __name__ == "__main__":
    import uvicorn  # noqa: E402

    uvicorn.run(app, host="0.0.0.0", port=8080)
