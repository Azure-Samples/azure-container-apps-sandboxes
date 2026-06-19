"""Simple anonymous web app in a sandbox (Python SDK).

Creates a sandbox on the `node-22` public disk, uploads the Node app from
the sibling ``app/`` directory, starts it on port 8080, exposes that port
anonymously (open to the internet), and verifies the response both from
inside the sandbox and from the host machine.

Reads configuration from ``samples/.env`` (written by
``python/samples/setup/setup.py`` or ``setup/cli/setup.sh``).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.containerapps.sandbox import (
    SandboxGroupClient,
    endpoint_for_region,
)

SCENARIO_DIR = Path(__file__).resolve().parent.parent
APP_DIR = SCENARIO_DIR / "app"
PORT = 8080


def _load_env() -> None:
    """Load samples/.env; exit with a friendly error if it isn't there yet."""
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


def _poll_in_sandbox(sandbox, url: str, timeout_s: int = 30) -> None:
    """Curl `url` from inside the sandbox until it returns 200 or we time out."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        result = sandbox.exec(
            f"curl -fsS -o /dev/null -w '%{{http_code}}' {url} || true"
        )
        last = (result.stdout or "").strip()
        if last == "200":
            return
        time.sleep(1)
    log = sandbox.exec("cat /tmp/node.log 2>/dev/null || true")
    raise RuntimeError(
        f"server not ready after {timeout_s}s (last http_code={last!r}); "
        f"node.log:\n{(log.stdout or '').strip()}"
    )


def _poll_public(url: str, timeout_s: int = 60) -> dict:
    """Fetch `url` from the host until it returns 200 + JSON or we time out."""
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
                last_err = f"http {resp.status}"
        except urllib.error.HTTPError as e:
            last_err = f"http {e.code}"
        except urllib.error.URLError as e:
            last_err = f"urlerror {e.reason}"
        time.sleep(2)
    raise RuntimeError(f"public URL not ready after {timeout_s}s (last: {last_err})")


def main() -> None:
    _load_env()
    disk = os.environ.get("ACA_WEBAPP_DISK", "node-22")

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(os.environ["ACA_SANDBOXGROUP_REGION"]),
        credential,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group=os.environ["ACA_RESOURCE_GROUP"],
        sandbox_group=os.environ["ACA_SANDBOX_GROUP"],
    )

    sandbox = None
    port_added = False
    try:
        print(f"==> Creating sandbox (disk={disk})...")
        sandbox = client.begin_create_sandbox(disk=disk).result()
        print(f"    sandbox: {sandbox.sandbox_id}")

        print(f"==> Uploading app from {APP_DIR.relative_to(SCENARIO_DIR.parent)}...")
        sandbox.exec("mkdir -p /app")
        sandbox.write_file("/app/server.js", (APP_DIR / "server.js").read_text())
        sandbox.write_file("/app/package.json", (APP_DIR / "package.json").read_text())

        print(f"==> Starting Node server on :{PORT}...")
        sandbox.exec(
            f"cd /app && nohup node server.js > /tmp/node.log 2>&1 &"
        )

        print("==> Polling in-sandbox readiness on /healthz...")
        _poll_in_sandbox(sandbox, f"http://localhost:{PORT}/healthz")
        print("    server is ready")

        print(f"==> add_port({PORT}, anonymous=True)")
        port = sandbox.add_port(PORT, anonymous=True)
        port_added = True
        url = getattr(port, "url", None)
        if not url:
            raise RuntimeError("add_port did not return a URL")
        print(f"    public URL: {url}")

        print("==> Verifying public URL (host-side)...")
        # `/` is HTML; JSON endpoints under /api/* and /healthz.
        deadline_html = None
        body_html = None
        for path in ("/healthz", "/api/hello", "/api/info"):
            body = _poll_public(url.rstrip("/") + path)
            print(f"    GET {path} -> {body}")
        # HTML smoke check on /
        import urllib.request as _u
        with _u.urlopen(url, timeout=10) as resp:
            assert resp.status == 200, resp.status
            ctype = resp.headers.get("Content-Type", "")
            assert "text/html" in ctype, ctype
            body_html = resp.read().decode("utf-8", errors="replace")
        assert "Hello from a sandbox" in body_html, "landing page missing greeting"
        print(f"    GET / -> 200 text/html ({len(body_html)} bytes, contains greeting)")

        # JSON-shape assertions on the API endpoints.
        hello = _poll_public(url.rstrip("/") + "/api/hello")
        assert hello.get("message") == "Hello from sandbox", hello
        assert "hostname" in hello and "uptime" in hello, hello
        health = _poll_public(url.rstrip("/") + "/healthz")
        assert health.get("status") == "ok", health
        info = _poll_public(url.rstrip("/") + "/api/info")
        assert "node" in info and "platform" in info, info
        print("==> All endpoint shape assertions passed.")

        print("==> Done.")
    finally:
        if sandbox is not None and port_added:
            try:
                print(f"==> remove_port({PORT})")
                sandbox.remove_port(PORT)
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
    main()
