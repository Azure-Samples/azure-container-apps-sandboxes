"""Excalidraw MCP server in an anonymous sandbox (Python SDK).

Creates a sandbox on the ``copilot`` disk, clones and builds
``excalidraw-mcp`` inside it, starts the server on port 80, exposes the
port anonymously (open to the internet), and verifies the public URL with
a real MCP ``initialize`` handshake over HTTPS.

Reads configuration from ``samples/.env`` (written by
``python/samples/setup/setup.py``).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.containerapps.sandbox import (
    SandboxGroupClient,
    endpoint_for_region,
)

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

REPO_URL = "https://github.com/excalidraw/excalidraw-mcp.git"
APP_DIR = "/home/user/mcp-app"
MCP_PORT = 80
INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "aca-samples-readiness-probe", "version": "1.0"},
    },
}


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it."""
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break
    if not os.environ.get("ACA_SANDBOXGROUP_REGION"):
        sys.exit(
            "error: samples/.env is missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


def _parse_mcp_response(body: str) -> dict[str, Any]:
    """MCP streamable-HTTP servers may reply as plain JSON or SSE-framed JSON.

    Accept both: if the body starts with ``event:`` or ``data:`` (SSE),
    extract the first JSON payload from a ``data:`` line.
    """
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise ValueError(f"unrecognized MCP response shape: {body[:200]!r}")


def _poll_mcp_in_sandbox(sandbox, *, timeout_s: int = 60) -> None:
    """curl POST /mcp from inside the sandbox until we get a valid initialize result."""
    deadline = time.monotonic() + timeout_s
    payload = json.dumps(INIT_REQUEST).replace("'", "'\\''")
    cmd = (
        f"curl -fsS -X POST http://localhost:{MCP_PORT}/mcp "
        f"-H 'Content-Type: application/json' "
        f"-H 'Accept: application/json, text/event-stream' "
        f"-d '{payload}' 2>&1"
    )
    last = ""
    while time.monotonic() < deadline:
        r = sandbox.exec(cmd)
        last = (r.stdout or "").strip()
        if r.exit_code == 0 and last:
            try:
                resp = _parse_mcp_response(last)
                if resp.get("result", {}).get("protocolVersion"):
                    return
            except (ValueError, json.JSONDecodeError):
                pass
        time.sleep(2)
    log = sandbox.exec("tail -50 /tmp/excalidraw.log 2>/dev/null || true")
    raise RuntimeError(
        f"excalidraw-mcp did not become ready after {timeout_s}s.\n"
        f"last response: {last[:300]!r}\n"
        f"server log:\n{(log.stdout or '').strip()}"
    )


def _mcp_initialize_public(url: str, *, timeout_s: int = 90) -> dict[str, Any]:
    """POST /mcp initialize from the host until we get a valid response."""
    body = json.dumps(INIT_REQUEST).encode()
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return _parse_mcp_response(resp.read().decode("utf-8", "replace"))
                last_err = f"http {resp.status}"
        except urllib.error.HTTPError as e:
            last_err = f"http {e.code}: {e.read()[:200]!r}"
        except urllib.error.URLError as e:
            last_err = f"urlerror {e.reason}"
        except (ValueError, json.JSONDecodeError) as e:
            last_err = f"parse error: {e}"
        time.sleep(3)
    raise RuntimeError(f"public MCP URL not ready after {timeout_s}s (last: {last_err})")


def main() -> int:
    _load_env()
    disk = os.environ.get("ACA_MCP_DISK", "copilot")

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(os.environ["ACA_SANDBOXGROUP_REGION"]),
        credential,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group=os.environ["ACA_RESOURCE_GROUP"],
        sandbox_group=os.environ["ACA_SANDBOX_GROUP"],
    )

    run_id = uuid.uuid4().hex[:8]
    labels = {"scenario": "mcp-hosting", "pattern": "excalidraw-anonymous", "run": run_id}

    sandbox = None
    port_added = False
    try:
        print(f"==> Creating sandbox (disk={disk}, run={run_id})...")
        sandbox = client.begin_create_sandbox(
            disk=disk, cpu="2000m", memory="4096Mi", labels=labels,
        ).result()
        print(f"    sandbox: {sandbox.sandbox_id}")

        print("==> Waiting for exec readiness...")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if sandbox.exec("true").exit_code == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("sandbox exec never came up")

        print("==> Cloning excalidraw-mcp...")
        sandbox.exec("npm config set strict-ssl false")
        r = sandbox.exec(
            f"git clone --depth=1 {REPO_URL} {APP_DIR} 2>&1 | tail -3"
        )
        if r.exit_code != 0:
            raise RuntimeError(f"git clone failed: {r.stdout}\n{r.stderr}")

        print("==> Installing dependencies (this can take ~60s)...")
        r = sandbox.exec(
            f"cd {APP_DIR} && npm install --ignore-optional --no-audit --no-fund 2>&1 | tail -3"
        )
        if r.exit_code != 0:
            raise RuntimeError(f"npm install failed: {r.stdout}\n{r.stderr}")

        print("==> Building...")
        r = sandbox.exec(f"cd {APP_DIR} && npm run build 2>&1 | tail -3")
        if r.exit_code != 0:
            raise RuntimeError(f"npm run build failed: {r.stdout}\n{r.stderr}")

        print(f"==> Starting MCP server on :{MCP_PORT}...")
        sandbox.exec(
            f"cd {APP_DIR} && PORT={MCP_PORT} nohup node dist/index.js "
            f"> /tmp/excalidraw.log 2>&1 & echo $! > /tmp/excalidraw.pid"
        )

        print("==> Probing in-sandbox MCP initialize handshake...")
        _poll_mcp_in_sandbox(sandbox)
        print("    in-sandbox /mcp is alive")

        print(f"==> add_port({MCP_PORT}, anonymous=True)...")
        port = sandbox.add_port(MCP_PORT, anonymous=True)
        port_added = True
        public_url = getattr(port, "url", None)
        if not public_url:
            raise RuntimeError("add_port did not return a URL")
        mcp_url = public_url.rstrip("/") + "/mcp"
        print(f"    public URL: {mcp_url}")

        print("==> Verifying public MCP URL (host-side initialize)...")
        result = _mcp_initialize_public(mcp_url)
        info = result.get("result", {})
        proto = info.get("protocolVersion", "?")
        server = info.get("serverInfo", {})
        print(f"    OK protocolVersion={proto} server={server.get('name', '?')}/{server.get('version', '?')}")

        print()
        print("=" * 72)
        print("EXCALIDRAW MCP DEPLOYED")
        print("=" * 72)
        print()
        print(f"MCP URL: {mcp_url}")
        print()
        print("Try it from THIS Copilot CLI session — ask me:")
        print(f'  "Register the MCP server at {mcp_url} for this session,')
        print('   list its tools, then draw a hello-world rectangle."')
        print()
        print("Try it from VS Code (.vscode/mcp.json):")
        print(json.dumps(
            {"servers": {"excalidraw": {"type": "http", "url": mcp_url}}},
            indent=2,
        ))
        print()
        print("Try it from Claude Desktop / ChatGPT:")
        print(f"  Settings -> Connectors -> Add custom -> {mcp_url}")
        print("=" * 72)
        print()

        try:
            input("Press Enter to delete the sandbox when you're done... ")
        except (EOFError, KeyboardInterrupt):
            print()

        return 0
    finally:
        if sandbox is not None and port_added:
            try:
                print(f"==> remove_port({MCP_PORT})")
                sandbox.remove_port(MCP_PORT)
            except Exception as e:
                print(f"    warning: remove_port failed: {e}")
        if sandbox is not None:
            try:
                print(f"==> Deleting sandbox {sandbox.sandbox_id}...")
                sandbox.delete()
            except Exception as e:
                print(f"    warning: delete failed: {e}")
        client.close()
        credential.close()


if __name__ == "__main__":
    sys.exit(main())
