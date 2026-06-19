"""postdeploy.py — finishes the runtime wiring for scenario 11.

Driven by `postdeploy.sh` / `postdeploy.ps1` so the same logic runs
on macOS / Linux / Windows. Reads `azd env get-values --output json`
on stdin (the wrappers pipe it in), uses the Azure SDK + Azure CLI
where appropriate.

Steps (idempotent — re-running is safe):

  1.  Read azd outputs (subscription, RG, namespace, MCP config,
      sandbox group, plus the operator-supplied SHAREPOINT_* and
      GITHUB_PAT env values).
  2.  Resolve the SharePoint MCP runtime URL from the namespace
      data plane.
  3.  Create or reuse the host sandbox in the sandbox group
      (we tag it `scenario=connectors-document-automation,
      role=listener` so re-runs find it).
  4.  Upload listener.py + prompt.md + requirements.txt +
      bootstrap.sh into the sandbox.
  5.  Apply egress policy: Deny default + Transform stamping
      X-API-Key on the MCP host, + Transform stamping
      Authorization on the three GitHub Copilot CLI hosts (mirrors
      scenario 10).
  6.  Run bootstrap.sh inside the sandbox (with all the SHAREPOINT_*
      and COPILOT_GITHUB_TOKEN env vars). Bootstrap installs the
      toolchain + starts uvicorn on :8080 as a detached process
      (sandboxes don't run systemd; we use nohup+setsid).
  7.  Register port 8080 with the ADC proxy: PUT /ports with
      `auth.entraId.objectIds = [<namespace MI principalId>]` so the
      namespace is the only caller the proxy will allow.
  8.  Create or update the namespace trigger config — callbackUrl =
      `https://<sandboxId>--8080.<region>.adcproxy.io`, metadata =
      `{sandboxGroupName, sandboxId}`.
  9.  Run the OAuth consent flow for the SharePoint connection and
      the workiqsharepoint MCP connection (opens browser tabs).
 10.  Print final summary + how to test.

Everything is best-effort idempotent. If step 5 fails because the
egress policy was already set, we log and continue. Same for the
role assignment (already done by Bicep) and the trigger config (uses
PUT).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

# Force stdout to UTF-8 so the ✓ in success logs (and other non-ASCII
# in subprocess output we tee through) doesn't crash on Windows where
# the default console encoding is cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("postdeploy")


# ---- Constants -----------------------------------------------------------

CONNECTOR_GATEWAY_API_VERSION = "2026-05-01-preview"
SANDBOX_GROUP_API_VERSION = "2026-02-01-preview"

# ADC proxy endpoint domain. The ports API and the per-sandbox proxy
# URL both live here. Per the HAR capture, port URLs follow the
# pattern: https://<sandboxId>--<port>.<region>.adcproxy.io
ADC_PROXY_HOST_TEMPLATE = "{sandbox_id}--{port}.{region}.adcproxy.io"

# Audience the namespace MUST mint its MI token for when calling the
# sandbox via the proxy. From the HAR.
ADC_PROXY_AUDIENCE = "https://auth.adcproxy.io/"

# GitHub host families Copilot CLI talks to — mirror scenario 10.
GITHUB_TRANSFORM_HOSTS = (
    ("api.github.com",                         "token",  "github-api-auth"),
    ("api.enterprise.githubcopilot.com",       "Bearer", "copilot-enterprise-auth"),
    ("telemetry.enterprise.githubcopilot.com", "Bearer", "copilot-telemetry-auth"),
)


import shutil

# On Windows, `az` is `az.cmd`, not `az.exe` — `subprocess.run(["az", ...])`
# fails with FileNotFoundError unless we resolve via shutil.which or
# pass shell=True. Resolve once at import.
_AZ = shutil.which("az") or shutil.which("az.cmd") or shutil.which("az.exe") or "az"


# ---- Shell helpers --------------------------------------------------------

def az_json(*args: str, allow_fail: bool = False) -> Any:
    """Invoke `az` and parse JSON output."""
    result = subprocess.run(
        [_AZ, *args, "--output", "json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        if allow_fail:
            return None
        log.error("az %s failed:\n%s", " ".join(args), result.stderr.strip())
        sys.exit(2)
    return json.loads(result.stdout) if result.stdout.strip() else None


def az_rest(method: str, uri: str, body: dict | None = None) -> Any:
    """Invoke `az rest` with a JSON body via a temp file (PowerShell
    on Windows mangles inline JSON — see scenario 10 notes)."""
    import tempfile
    args = [_AZ, "rest", "--method", method, "--uri", uri]
    if body is not None:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as f:
            json.dump(body, f)
            body_path = f.name
        args += ["--body", f"@{body_path}", "--headers", "Content-Type=application/json"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        log.error("az rest %s %s failed:\n%s", method, uri, result.stderr.strip())
        sys.exit(2)
    return json.loads(result.stdout) if result.stdout.strip() else None


def sandbox_exec(sandbox: Any, command: str, *, timeout_s: float = 120) -> Any:
    """Exec a shell command inside the sandbox via the SDK."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        sandbox.exec(command, timeout=timeout_s)
    )


# ---- Main steps -----------------------------------------------------------

def step_resolve_inputs() -> dict[str, Any]:
    log.info("==> [1/9] reading azd outputs")
    raw = subprocess.run(
        ["azd", "env", "get-values", "--output", "json"],
        capture_output=True, text=True, check=True,
    )
    out = json.loads(raw.stdout)
    required = (
        "AZURE_SUBSCRIPTION_ID",
        "resourceGroupName",
        "connectorGatewayName",
        "connectorGatewayId",
        "gatewayPrincipalId",
        "sharepointConnectionName",
        "sharepointMcpConnectionName",
        "sharepointMcpServerConfigName",
        "sandboxGroupName",
        "sandboxGroupId",
        "sandboxGroupRegion",
        "tenantId",
    )
    missing = [k for k in required if not out.get(k)]
    if missing:
        log.error("missing azd outputs: %s", missing)
        sys.exit(2)

    # Operator-supplied via `azd env set`
    out.setdefault("SHAREPOINT_SITE_URL", "")
    out.setdefault("SHAREPOINT_LIBRARY_ID", "")
    out.setdefault("SHAREPOINT_INPUT_FOLDER", "")          # empty = process whole library
    out.setdefault("SHAREPOINT_OUTPUT_FOLDER", "Extracted")
    out.setdefault("GITHUB_PAT", "")
    return out


def step_resolve_mcp_url(cfg: dict[str, Any]) -> str:
    log.info("==> [2/9] resolving SharePoint MCP runtime URL")
    arm = (
        f"https://management.azure.com{cfg['connectorGatewayId']}"
        f"/mcpserverConfigs/{cfg['sharepointMcpServerConfigName']}"
        f"?api-version={CONNECTOR_GATEWAY_API_VERSION}"
    )
    resp = az_rest("get", arm)
    url = resp["properties"].get("mcpEndpointUrl")
    if not url:
        log.error("mcpserverConfig has no mcpEndpointUrl yet: %s", resp)
        sys.exit(2)
    log.info("    MCP URL: %s", url)
    return url


def step_get_or_create_host_sandbox(cfg: dict[str, Any]) -> tuple[Any, str]:
    """Find or create the long-lived host sandbox in the group.

    Returns (sandbox_client, sandbox_id). The sandbox client is the
    azure-containerapps-sandbox SDK object that exposes .exec,
    .write_file, .set_egress_default, etc.
    """
    log.info("==> [3/9] creating or reusing host sandbox in %s", cfg["sandboxGroupName"])
    import asyncio
    from azure.identity.aio import DefaultAzureCredential
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.containerapps.sandbox import endpoint_for_region

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    cred = DefaultAzureCredential()
    sg_client = SandboxGroupClient(
        endpoint=endpoint_for_region(cfg["sandboxGroupRegion"]),
        credential=cred,
        subscription_id=cfg["AZURE_SUBSCRIPTION_ID"],
        resource_group=cfg["resourceGroupName"],
        sandbox_group=cfg["sandboxGroupName"],
    )

    target_labels = {
        "scenario": "connectors-document-automation",
        "role": "listener",
    }

    async def find_or_create() -> tuple[Any, str]:
        async for sbx in sg_client.list_sandboxes():
            labels = getattr(sbx, "labels", None) or {}
            if labels.get("role") == "listener" and labels.get("scenario") == "connectors-document-automation":
                log.info("    reusing existing sandbox id=%s state=%s", sbx.id, getattr(sbx, "state", "?"))
                # list_sandboxes returns metadata only; get_sandbox_client
                # gives us an operational client (exec, write_file, ...).
                client = sg_client.get_sandbox_client(sbx.id)
                # OnDemand-activation sandboxes may be Stopped between
                # trigger events. We need it Running to upload code
                # and run bootstrap. ensure_running is idempotent.
                await client.ensure_running()
                return client, sbx.id
        log.info("    no existing listener sandbox found; creating one")
        poller = await sg_client.begin_create_sandbox(
            disk="ubuntu", cpu="2000m", memory="4096Mi",
            labels=target_labels,
        )
        created = await poller.result()
        sandbox_id = getattr(created, "id", None) or getattr(created, "sandbox_id", None)
        log.info("    sandbox created id=%s", sandbox_id)
        client = sg_client.get_sandbox_client(sandbox_id)
        await client.ensure_running()
        return client, sandbox_id

    sandbox, sandbox_id = loop.run_until_complete(find_or_create())
    return sandbox, sandbox_id


def step_upload_listener(sandbox: Any) -> None:
    log.info("==> [4/9] uploading listener code to sandbox")
    import asyncio
    here = Path(__file__).resolve().parent.parent.parent / "host"
    files = ("listener.py", "prompt.md", "requirements.txt", "bootstrap.sh")
    async def upload_all() -> None:
        await sandbox.exec("mkdir -p /opt/listener /work")
        for name in files:
            src = here / name
            data = src.read_bytes()
            # bootstrap.sh and any other shell script MUST have LF
            # line endings. On Windows, git checkout converts to CRLF
            # by default; uploaded as-is, `bash` errors with
            #   "$'\r': command not found" and "set: pipefail: invalid option"
            # because the CR is part of the first command word.
            if name.endswith((".sh", ".py")):
                data = data.replace(b"\r\n", b"\n")
            dst = f"/opt/listener/{name}"
            await sandbox.write_file(dst, data)
        await sandbox.exec("chmod +x /opt/listener/bootstrap.sh")
    asyncio.get_event_loop().run_until_complete(upload_all())


def step_apply_egress(sandbox: Any, cfg: dict[str, Any], mcp_url: str) -> None:
    log.info("==> [5/9] applying egress policy (deny-default + transforms)")
    import asyncio
    from azure.containerapps.sandbox import EgressHeader

    mcp_host = mcp_url.split("://", 1)[1].split("/", 1)[0]
    pat = cfg.get("GITHUB_PAT", "")

    async def apply() -> None:
        try:
            await sandbox.set_egress_default("Deny")
        except Exception as exc:  # noqa: BLE001
            log.warning("    set_egress_default failed (probably already set): %s", exc)
        try:
            await sandbox.add_egress_transform_rule(
                host=mcp_host,
                headers=[EgressHeader(operation="Set", name="X-API-Key", value=_get_api_key(cfg))],
                name="mcp-api-key",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("    mcp transform rule add failed (probably already exists): %s", exc)
        if pat:
            for host, scheme, name in GITHUB_TRANSFORM_HOSTS:
                try:
                    await sandbox.add_egress_transform_rule(
                        host=host,
                        headers=[EgressHeader(operation="Set", name="Authorization", value=f"{scheme} {pat}")],
                        name=name,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("    %s transform rule add failed: %s", host, exc)
        else:
            log.warning(
                "    GITHUB_PAT is empty — Copilot CLI inside the sandbox "
                "will fail. Set it with `azd env set GITHUB_PAT <token>` "
                "and re-run azd hooks run postdeploy."
            )
    asyncio.get_event_loop().run_until_complete(apply())


def _get_api_key(cfg: dict[str, Any]) -> str:
    """Issue a never-expiring MCP-scoped runtime API key."""
    arm = (
        f"https://management.azure.com/subscriptions/{cfg['AZURE_SUBSCRIPTION_ID']}"
        f"/resourceGroups/{cfg['resourceGroupName']}"
        f"/providers/Microsoft.Web/connectorGateways/{cfg['connectorGatewayName']}"
        f"/listApiKey?api-version={CONNECTOR_GATEWAY_API_VERSION}"
    )
    resp = az_rest("post", arm, body={
        "scope": cfg["sharepointMcpServerConfigName"],
        "neverExpire": True,
    })
    key = resp.get("key")
    if not key:
        log.error("listApiKey returned no key: %s", resp)
        sys.exit(2)
    return key


def step_run_bootstrap(sandbox: Any, cfg: dict[str, Any], mcp_url: str) -> None:
    log.info("==> [6/9] running bootstrap.sh inside sandbox (~30-90s)")
    import asyncio

    # On re-runs, the sandbox already has the Deny egress + transforms
    # from a previous step_apply_egress. apt-get / pip / curl all hit
    # hosts that aren't in the allowlist, so reset to Allow before
    # running bootstrap. step_apply_egress re-applies Deny + transforms
    # afterwards.
    async def open_egress() -> None:
        try:
            await sandbox.set_egress_default("Allow")
        except Exception as exc:  # noqa: BLE001
            log.warning("    set_egress_default(Allow) before bootstrap failed (continuing): %s", exc)
    asyncio.get_event_loop().run_until_complete(open_egress())

    env_exports = " ".join(
        f"{k}='{v}'" for k, v in {
            "SHAREPOINT_MCP_URL": mcp_url,
            "SHAREPOINT_SITE_URL": cfg.get("SHAREPOINT_SITE_URL", ""),
            "SHAREPOINT_LIBRARY_ID": cfg.get("SHAREPOINT_LIBRARY_ID", ""),
            "SHAREPOINT_INPUT_FOLDER": cfg.get("SHAREPOINT_INPUT_FOLDER", ""),
            "SHAREPOINT_OUTPUT_FOLDER": cfg.get("SHAREPOINT_OUTPUT_FOLDER", "Extracted"),
            "COPILOT_GITHUB_TOKEN": cfg.get("GITHUB_PAT", ""),
        }.items()
    )
    cmd = f"bash -lc \"{env_exports} bash /opt/listener/bootstrap.sh\""

    async def run() -> None:
        r = await sandbox.exec(cmd, timeout=600)
        if r.exit_code != 0:
            log.error("bootstrap failed exit=%d\nstdout:\n%s\nstderr:\n%s",
                      r.exit_code, (r.stdout or "")[:4000], (r.stderr or "")[:1000])
            sys.exit(2)
        log.info("    bootstrap ok\n%s", (r.stdout or "").splitlines()[-1] if r.stdout else "")
    asyncio.get_event_loop().run_until_complete(run())


def step_register_port(cfg: dict[str, Any], sandbox_id: str) -> str:
    log.info("==> [7/9] registering port 8080 with ADC proxy (Entra-restricted to namespace MI)")
    # The Python SDK's add_port / update_ports only exposes
    # PortAuthEntraId(enabled, emails) — no objectIds/tenantIds field
    # for restricting to a specific service principal. We need
    # objectIds to lock the port down to the namespace MI (per the HAR
    # capture). Hit the REST endpoint directly with httpx + a
    # DefaultAzureCredential token scoped to the data plane.
    import asyncio
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    region = cfg["sandboxGroupRegion"]
    proxy_host = ADC_PROXY_HOST_TEMPLATE.format(sandbox_id=sandbox_id, port=8080, region=region)
    url = f"https://{proxy_host}"
    body = {
        "ports": [{
            "port": 8080,
            "url": url,
            "auth": {
                "anonymous": False,
                "entraId": {
                    "enabled": True,
                    "objectIds": [cfg["gatewayPrincipalId"]],
                    "tenantIds": [cfg["tenantId"]],
                },
            },
            "activationMode": "OnDemand",
            "protocol": "Http",
        }],
    }
    arm = (
        f"https://management.{region}.azuredevcompute.io"
        f"/subscriptions/{cfg['AZURE_SUBSCRIPTION_ID']}"
        f"/resourceGroups/{cfg['resourceGroupName']}"
        f"/sandboxGroups/{cfg['sandboxGroupName']}"
        f"/sandboxes/{sandbox_id}/ports"
        f"?api-version={SANDBOX_GROUP_API_VERSION}"
    )

    async def do_put() -> None:
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token("https://dynamicsessions.io/.default")
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.put(
                    arm,
                    json=body,
                    headers={"Authorization": f"Bearer {tok.token}",
                             "Content-Type": "application/json"},
                )
            if resp.status_code >= 400:
                log.error("PUT /ports failed status=%d body=%s", resp.status_code, resp.text)
                sys.exit(2)
        finally:
            await cred.close()
    asyncio.get_event_loop().run_until_complete(do_put())
    log.info("    port URL: %s", url)
    return url


def step_create_trigger(cfg: dict[str, Any], sandbox_id: str, callback_url: str) -> None:
    log.info("==> [8/9] creating/updating trigger config")
    # Defaults — operator can override via azd env set
    site_url = cfg.get("SHAREPOINT_SITE_URL") or ""
    library_id = cfg.get("SHAREPOINT_LIBRARY_ID") or ""
    if not site_url or not library_id:
        log.warning(
            "    SHAREPOINT_SITE_URL or SHAREPOINT_LIBRARY_ID is empty; "
            "trigger config will be created without `parameters` "
            "and you'll need to fill them in manually via the portal."
        )
    trigger_name = f"on-new-file-{sandbox_id[:8]}"
    arm = (
        f"https://management.azure.com{cfg['connectorGatewayId']}"
        f"/triggerConfigs/{trigger_name}"
        f"?api-version={CONNECTOR_GATEWAY_API_VERSION}"
    )
    body = {
        "location": cfg["sandboxGroupRegion"],  # informational
        "properties": {
            "state": "Enabled",
            "description": (
                "When a new file is created in the configured SharePoint "
                "library, POST its dynamicProperties directly to the host "
                "sandbox's listener on port 8080."
            ),
            "connectionDetails": {
                "connectorName": "sharepointonline",
                "connectionName": cfg["sharepointConnectionName"],
            },
            "operationName": "GetOnNewFileItems",
            "parameters": [
                {"name": "dataset", "value": site_url},
                {"name": "table", "value": library_id},
            ] if site_url and library_id else [],
            "notificationDetails": {
                "callbackUrl": callback_url,
                "httpMethod": "POST",
                "body": "@triggerBody()",
                "authentication": {
                    "type": "ManagedServiceIdentity",
                    "audience": ADC_PROXY_AUDIENCE,
                },
            },
            "metadata": {
                "sandboxGroupName": cfg["sandboxGroupName"],
                "sandboxId": sandbox_id,
                # Poll cadence — Connector Namespaces dispatches the
                # trigger this often. 10s is aggressive (the SharePoint
                # connector still does its own change-detection under
                # the hood, so this is a lower-bound, not a guaranteed
                # cadence). Drop back to Minute/1 if you hit gateway
                # throttling.
                "recurrenceFrequency": "Second",
                "recurrenceInterval": "10",
            },
        },
    }
    az_rest("put", arm, body=body)
    log.info("    trigger config: %s (poll every 10s)", trigger_name)


def _connection_arm_base(cfg: dict[str, Any], connection_name: str) -> str:
    return (
        f"https://management.azure.com/subscriptions/{cfg['AZURE_SUBSCRIPTION_ID']}"
        f"/resourceGroups/{cfg['resourceGroupName']}"
        f"/providers/Microsoft.Web/connectorGateways/{cfg['connectorGatewayName']}"
        f"/connections/{connection_name}"
    )


def _connection_status(cfg: dict[str, Any], connection_name: str) -> str:
    """Returns the overallStatus of a connection, or '' if it can't be read."""
    try:
        r = az_rest(
            "get",
            f"{_connection_arm_base(cfg, connection_name)}?api-version={CONNECTOR_GATEWAY_API_VERSION}",
        )
        return (r.get("properties", {}).get("overallStatus") or "").strip()
    except SystemExit:
        return ""


def _authorize_one_connection(cfg: dict[str, Any], connection_name: str, label: str) -> None:
    """Run the official OAuth consent flow for a Connector Namespaces
    connection, using only ARM REST APIs (no third-party CLI ext).

    Flow:
      1. POST .../connections/<name>/listConsentLinks → { value: [{ link }] }
      2. Open `link` in the user's browser. The link is a self-
         contained logic-apis consent page that handles the OAuth
         dance + token persistence inside the connector runtime.
         There is no redirect back to our app — once the user
         completes consent on that page, the connection's
         `overallStatus` flips to `Connected` server-side.
      3. Poll the connection's overallStatus until `Connected`
         (or timeout).

    No loopback HTTP server, no confirmConsentCode call. The unofficial
    `connector-namespace` az CLI ext we used to depend on does the
    same thing under the hood. confirmConsentCode is a separate flow
    used by custom connectors that issue their own consent codes —
    not by managed connectors like sharepointonline / workiqsharepoint.
    """
    log.info("    %s (%s)", label, connection_name)

    if _connection_status(cfg, connection_name).lower() == "connected":
        log.info("      already Connected; skipping consent flow")
        return

    me = az_json("ad", "signed-in-user", "show", "--query", "id")
    if not me:
        log.warning("      could not resolve signed-in user objectId; skipping")
        return
    object_id = me if isinstance(me, str) else str(me)
    tenant_id = cfg["tenantId"]

    # 1. Ask the namespace for a consent link.
    #
    # The Logic-Apps consent flow IS a real OAuth handshake with a
    # `state` query param round-trip — it really does navigate back
    # to the configured redirectUrl after the user signs in. The
    # consent service has special-cased a few known-safe redirect
    # URLs for "no app to redirect to" scenarios; the canonical
    # one is `https://portal.azure.com` (what az portal uses, and
    # what the connector-namespaces consent service expects for
    # CLI-driven flows). After the user signs in, the browser lands
    # on portal.azure.com and the consent service has already
    # persisted the OAuth tokens server-side. We just poll the
    # connection's overallStatus until it flips to Connected.
    #
    # The request body is intentionally minimal — `objectId` and
    # `tenantId` are inferred from the calling principal's bearer
    # token and don't need to be in the body.
    arm_base = _connection_arm_base(cfg, connection_name)
    link_resp = az_rest(
        "post",
        f"{arm_base}/listConsentLinks?api-version={CONNECTOR_GATEWAY_API_VERSION}",
        body={
            "parameters": [{
                "parameterName": "token",
                "redirectUrl": "https://portal.azure.com",
            }],
        },
    )
    if not link_resp or not link_resp.get("value"):
        log.warning("      listConsentLinks returned no links; skipping")
        return
    link = link_resp["value"][0].get("link")
    if not link:
        log.warning("      listConsentLinks returned no `link` field; skipping")
        return

    # 2. Open it. Also print the URL as a fallback for environments
    # where webbrowser.open is blocked (e.g., Windows Security).
    log.info("      opening browser for OAuth consent...")
    log.info("      (if no tab opens, paste this URL manually:\n         %s)", link)
    import webbrowser
    try:
        webbrowser.open(link)
    except Exception as exc:  # noqa: BLE001
        log.warning("      webbrowser.open failed (%s); paste the link manually", exc)

    # 3. Poll for Connected. The consent page commits the OAuth
    # tokens server-side; we just have to wait for the connection
    # to reflect the new state. ~5 min cap, 3s cadence.
    import time as _time
    deadline = _time.time() + 300
    last_status = ""
    while _time.time() < deadline:
        s = _connection_status(cfg, connection_name)
        if s != last_status:
            log.info("      status: %s", s or "?")
            last_status = s
        if s.lower() == "connected":
            log.info("      \u2713 %s authenticated", connection_name)
            return
        _time.sleep(3)
    log.warning("      timed out waiting for consent (5 min). Re-run "
                "`python infra/scripts/postdeploy.py --skip-oauth=false` "
                "when you're ready to complete the browser flow.")


def step_authorize_connections(cfg: dict[str, Any]) -> None:
    log.info("==> [9/9] authorizing connections (browser tabs)")
    for conn_name, label in (
        (cfg["sharepointConnectionName"], "SharePoint Online (trigger)"),
        (cfg["sharepointMcpConnectionName"], "SharePoint MCP (sandbox -> SP)"),
    ):
        try:
            _authorize_one_connection(cfg, conn_name, label)
        except Exception as exc:  # noqa: BLE001
            log.warning("    %s: authorization failed (%s); continuing", conn_name, exc)


def step_summary(cfg: dict[str, Any], sandbox_id: str, callback_url: str) -> None:
    log.info(textwrap.dedent(f"""
        =============================================================
         ALL DONE
        =============================================================
         Drop a PDF into the configured SharePoint library / folder.
         The Connector Namespace polls once a minute and POSTs each new
         file's properties directly to:

           {callback_url}

         Watch the listener inside the sandbox with:

           # bash (Linux/macOS) — needs `az` + recent Azure SDK
           az containerapp sandbox exec \\
             --resource-group {cfg['resourceGroupName']} \\
             --sandbox-group-name {cfg['sandboxGroupName']} \\
             --sandbox-id {sandbox_id} \\
             --command 'journalctl -u listener.service -f'

         Tear down with:
           azd down --purge --force --no-prompt
    """))


# ---- Entry point ----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-oauth", action="store_true",
        help="Skip the browser-based OAuth consent step (useful in CI re-runs).",
    )
    args = ap.parse_args()

    cfg = step_resolve_inputs()
    mcp_url = step_resolve_mcp_url(cfg)
    sandbox, sandbox_id = step_get_or_create_host_sandbox(cfg)
    step_upload_listener(sandbox)
    # Run bootstrap BEFORE applying egress lockdown. apt-get update +
    # pip install + copilot-install all hit hosts (archive.ubuntu.com,
    # pypi.org, gh.io, etc.) that the deny-default policy would block
    # unless we maintained an explicit allowlist. Same ordering scenario
    # 10's receiver uses for its Copilot install.
    step_run_bootstrap(sandbox, cfg, mcp_url)
    step_apply_egress(sandbox, cfg, mcp_url)
    callback_url = step_register_port(cfg, sandbox_id)
    step_create_trigger(cfg, sandbox_id, callback_url)
    if not args.skip_oauth:
        step_authorize_connections(cfg)
    step_summary(cfg, sandbox_id, callback_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
