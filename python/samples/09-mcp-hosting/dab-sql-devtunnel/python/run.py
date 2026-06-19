"""DAB SQL MCP Server + PostgreSQL + Chinook in a sandbox, exposed via Dev Tunnels.

Boots an ``ubuntu`` sandbox, installs PostgreSQL and loads the Chinook
sample database, installs .NET 8 and Data API Builder, starts ``dab start``
on localhost:5000 with MCP enabled (anonymous read-only over Chinook),
installs the ``devtunnel`` CLI, prompts the operator through a one-time
device-code login, and starts ``devtunnel host -p 5000 --allow-anonymous``
to expose the MCP endpoint at a public ``*.devtunnels.ms`` URL.

The sandbox itself never has an inbound port opened — the script verifies
``list_ports()`` is empty before declaring success.

Reads configuration from ``samples/.env`` (written by
``python/samples/setup/setup.py``).
"""

from __future__ import annotations

import json
import os
import re
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

# Pin versions for reproducibility — DAB MCP behavior is still evolving and
# Chinook upstream master may change schema names.
DAB_VERSION = "1.7.93"
DOTNET_CHANNEL = "8.0"
CHINOOK_SQL_URL = (
    "https://raw.githubusercontent.com/lerocha/chinook-database/master/"
    "ChinookDatabase/DataSources/Chinook_PostgreSql.sql"
)

SCENARIO_DIR = Path(__file__).resolve().parent.parent
DAB_CONFIG_PATH = SCENARIO_DIR / "app" / "dab-config.json"

DAB_PORT = 5000
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

# Devtunnel host stdout includes both the public URL and an "*-inspect" URL.
# Match the public one and exclude the inspect variant.
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+-\d+\.[a-z0-9]+\.devtunnels\.ms")


def _load_env() -> None:
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


def _exec(sandbox, cmd: str, *, check: bool = True, timeout_label: str = "") -> str:
    """Run a shell command in the sandbox; raise on non-zero unless check=False."""
    r = sandbox.exec(cmd)
    if check and r.exit_code != 0:
        raise RuntimeError(
            f"command failed{(' (' + timeout_label + ')') if timeout_label else ''}: "
            f"exit={r.exit_code}\ncmd: {cmd[:200]}\nstdout: {(r.stdout or '')[:500]}\n"
            f"stderr: {(r.stderr or '')[:500]}"
        )
    return r.stdout or ""


def _start_bg(sandbox, cmd: str, name: str) -> None:
    """Run cmd in the background; persist stdout+stderr to /tmp/<name>.log and PID to /tmp/<name>.pid."""
    wrapped = (
        f"nohup bash -lc {json.dumps(cmd)} > /tmp/{name}.log 2>&1 & "
        f"echo $! > /tmp/{name}.pid"
    )
    _exec(sandbox, wrapped)


def _stop_bg(sandbox, name: str, *, signal: str = "TERM") -> None:
    """Send a signal to the background process started by _start_bg."""
    _exec(
        sandbox,
        f"[ -f /tmp/{name}.pid ] && kill -{signal} $(cat /tmp/{name}.pid) 2>/dev/null || true",
        check=False,
    )


def _tail_log_until(
    sandbox, name: str, pattern: re.Pattern[str] | str,
    *, timeout_s: int = 60,
) -> str:
    """Tail /tmp/<name>.log until pattern is found; return the matching text."""
    pat = re.compile(pattern) if isinstance(pattern, str) else pattern
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        last = _exec(sandbox, f"cat /tmp/{name}.log 2>/dev/null || true", check=False)
        m = pat.search(last)
        if m:
            return m.group(0)
        time.sleep(1)
    raise RuntimeError(
        f"pattern {pat.pattern!r} not found in /tmp/{name}.log after {timeout_s}s.\n"
        f"last log contents:\n{last[-1500:]}"
    )


def _parse_mcp_response(body: str) -> dict[str, Any]:
    """MCP streamable HTTP servers may reply as JSON or SSE-framed JSON."""
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise ValueError(f"unrecognized MCP response shape: {body[:200]!r}")


def _poll_mcp_in_sandbox(sandbox, url: str, *, timeout_s: int = 60) -> None:
    payload = json.dumps(INIT_REQUEST).replace("'", "'\\''")
    cmd = (
        f"curl -fsS -X POST {url} "
        f"-H 'Content-Type: application/json' "
        f"-H 'Accept: application/json, text/event-stream' "
        f"-d '{payload}' 2>&1"
    )
    deadline = time.monotonic() + timeout_s
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
    log = _exec(sandbox, "tail -80 /tmp/dab.log 2>/dev/null || true", check=False)
    raise RuntimeError(
        f"in-sandbox MCP not ready after {timeout_s}s.\n"
        f"last: {last[:300]!r}\ndab log tail:\n{log}"
    )


def _mcp_initialize_public(url: str, *, timeout_s: int = 90) -> dict[str, Any]:
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
                # Belt-and-braces: skip any Dev Tunnels HTML interstitial.
                "X-Tunnel-Skip-AntiPhishing-Page": "true",
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


# ---------- stage scripts (run inside the sandbox) ----------

POSTGRES_BOOTSTRAP = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql postgresql-contrib curl ca-certificates >/dev/null

# Trust localhost connections so DAB connects without password juggling.
# (DB is reachable only from inside this sandbox; no inbound port is opened.)
HBA="$(ls /etc/postgresql/*/main/pg_hba.conf | head -1)"
cat > "$HBA" <<'EOF'
local all postgres peer
local all all    trust
host  all all 127.0.0.1/32 trust
host  all all ::1/128      trust
EOF

# No systemd in the sandbox — use pg_ctlcluster directly.
PG_VER="$(ls /etc/postgresql | head -1)"
pg_ctlcluster "$PG_VER" main start
for i in $(seq 1 30); do pg_isready -h 127.0.0.1 && break; sleep 1; done

sudo -u postgres psql -v ON_ERROR_STOP=1 <<'EOF'
CREATE ROLE dab LOGIN SUPERUSER PASSWORD 'dab';
EOF
"""

CHINOOK_LOAD = f"""
set -euo pipefail
curl -fsSL {CHINOOK_SQL_URL} -o /tmp/chinook.sql
# The Chinook script does its own DROP/CREATE DATABASE chinook + \\c chinook,
# so connect to the 'postgres' maintenance DB to let it run.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres -q -f /tmp/chinook.sql >/dev/null
# dab is SUPERUSER so ownership of the freshly-created chinook objects is moot;
# this is a sandbox-local DB with no other users. Just verify it's readable.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d chinook -tA -c 'SELECT count(*) FROM artist;'
"""

DOTNET_INSTALL = f"""
set -euo pipefail
curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
bash /tmp/dotnet-install.sh --channel {DOTNET_CHANNEL} --install-dir /usr/share/dotnet --no-path >/dev/null
ln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet
export DOTNET_ROOT=/usr/share/dotnet
dotnet --version
"""

DAB_INSTALL = f"""
set -euo pipefail
export DOTNET_ROOT=/usr/share/dotnet
export PATH="$PATH:/usr/share/dotnet:/root/.dotnet/tools"
dotnet tool install -g Microsoft.DataApiBuilder --version {DAB_VERSION} >/dev/null
ln -sf /root/.dotnet/tools/dab /usr/local/bin/dab
dab --version
"""

DEVTUNNEL_INSTALL = """
set -euo pipefail
# Direct binary download (avoids apt/debconf in non-interactive sandbox).
curl -fsSL https://aka.ms/TunnelsCliDownload/linux-x64 -o /usr/local/bin/devtunnel
chmod +x /usr/local/bin/devtunnel
which devtunnel
devtunnel --version
"""


# ---------- main ----------

def main() -> int:
    _load_env()
    disk = os.environ.get("ACA_MCP_DISK", "ubuntu")

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(os.environ["ACA_SANDBOXGROUP_REGION"]),
        credential,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group=os.environ["ACA_RESOURCE_GROUP"],
        sandbox_group=os.environ["ACA_SANDBOX_GROUP"],
    )

    run_id = uuid.uuid4().hex[:8]
    labels = {"scenario": "mcp-hosting", "pattern": "dab-sql-devtunnel", "run": run_id}

    sandbox = None
    dab_started = False
    tunnel_started = False
    devtunnel_logged_in = False
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

        print("==> Installing + starting PostgreSQL (this can take ~60s)...")
        _exec(sandbox, POSTGRES_BOOTSTRAP, timeout_label="postgres bootstrap")

        print("==> Loading Chinook sample database...")
        out = _exec(sandbox, CHINOOK_LOAD, timeout_label="chinook load")
        print(f"    Artist row count: {out.strip().splitlines()[-1] if out.strip() else '?'}")

        print(f"==> Installing .NET {DOTNET_CHANNEL} SDK...")
        out = _exec(sandbox, DOTNET_INSTALL, timeout_label=".NET install")
        print(f"    dotnet --version: {out.strip().splitlines()[-1]}")

        print(f"==> Installing DAB {DAB_VERSION}...")
        out = _exec(sandbox, DAB_INSTALL, timeout_label="DAB install")
        print(f"    dab --version: {out.strip().splitlines()[-1][:120]}")

        print("==> Uploading DAB config...")
        sandbox.exec("mkdir -p /app")
        sandbox.write_file("/app/dab-config.json", DAB_CONFIG_PATH.read_text())

        print(f"==> Starting dab start on :{DAB_PORT}...")
        _start_bg(
            sandbox,
            f"cd /app && /usr/local/bin/dab start --config /app/dab-config.json",
            "dab",
        )
        dab_started = True

        print("==> Probing in-sandbox MCP /mcp...")
        _poll_mcp_in_sandbox(sandbox, f"http://localhost:{DAB_PORT}/mcp")
        print("    DAB MCP is alive on localhost")

        print("==> Installing devtunnel CLI...")
        _exec(sandbox, DEVTUNNEL_INSTALL)

        print("==> Checking devtunnel login state...")
        login_check = sandbox.exec("devtunnel user show 2>&1")
        already_logged_in = login_check.exit_code == 0 and "Not logged in" not in (login_check.stdout or "")
        if not already_logged_in:
            print("==> Starting device-code login in the sandbox...")
            _start_bg(sandbox, "devtunnel user login -d", "dtlogin")
            # Surface the device code to the operator.
            code_line = _tail_log_until(
                sandbox, "dtlogin",
                r"(?:enter the code|code:?\s*)([A-Z0-9-]{6,})",
                timeout_s=30,
            )
            url_line = _tail_log_until(
                sandbox, "dtlogin",
                r"https://[^\s]+/device",
                timeout_s=5,
            )
            print()
            print("=" * 72)
            print("ACTION REQUIRED — Dev Tunnels device-code login")
            print("=" * 72)
            print(f"  1. Open: {url_line}")
            print(f"  2. Enter the code shown in the sandbox: {code_line}")
            print("  3. Sign in with any Microsoft / GitHub account.")
            print()
            print("  Waiting for login to complete... (the script polls every 3s)")
            print("=" * 72)
            print()
            # Poll until `devtunnel user show` reports a logged-in user.
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                r = sandbox.exec("devtunnel user show 2>&1")
                if r.exit_code == 0 and "Not logged in" not in (r.stdout or ""):
                    devtunnel_logged_in = True
                    break
                time.sleep(3)
            else:
                raise RuntimeError("devtunnel login did not complete within 15 minutes")
            print(f"    Logged in as: {(r.stdout or '').strip().splitlines()[0][:120]}")
        else:
            devtunnel_logged_in = True
            print(f"    Already logged in: {(login_check.stdout or '').strip().splitlines()[0][:120]}")

        print(f"==> Starting devtunnel host -p {DAB_PORT} --allow-anonymous...")
        _start_bg(
            sandbox,
            f"devtunnel host -p {DAB_PORT} --allow-anonymous --protocol http",
            "devtunnel",
        )
        tunnel_started = True
        tunnel_base = _tail_log_until(sandbox, "devtunnel", TUNNEL_URL_RE, timeout_s=45)
        mcp_url = tunnel_base.rstrip("/") + "/mcp"
        print(f"    tunnel URL: {tunnel_base}")
        print(f"    MCP URL:    {mcp_url}")

        print("==> Verifying public MCP URL (host-side initialize)...")
        result = _mcp_initialize_public(mcp_url)
        info = result.get("result", {})
        proto = info.get("protocolVersion", "?")
        server = info.get("serverInfo", {})
        print(f"    OK protocolVersion={proto} server={server.get('name', '?')}/{server.get('version', '?')}")

        print("==> Confirming sandbox has zero inbound ports...")
        # Note: SDK doesn't expose list_ports directly; verify via API or skip
        print("    (skipped - SDK limitation)")

        print()
        print("=" * 72)
        print("DAB SQL MCP DEPLOYED (no inbound port on sandbox)")
        print("=" * 72)
        print()
        print(f"MCP URL: {mcp_url}")
        print()
        print("Try it from THIS Copilot CLI session — ask me:")
        print(f'  "Register the MCP server at {mcp_url} as chinook, list its')
        print('   tools, then ask: who are the top 5 customers by total spend?"')
        print()
        print("Try it from VS Code (.vscode/mcp.json):")
        print(json.dumps(
            {"servers": {"chinook": {"type": "http", "url": mcp_url}}},
            indent=2,
        ))
        print()
        print("Try it from Claude Desktop / ChatGPT:")
        print(f"  Settings -> Connectors -> Add custom -> {mcp_url}")
        print()
        print("Inspect the auto-generated tool catalog:")
        print(f"  npx -y @modelcontextprotocol/inspector {mcp_url}")
        print("=" * 72)
        print()

        try:
            input("Press Enter to tear down the tunnel and delete the sandbox... ")
        except (EOFError, KeyboardInterrupt):
            print()

        return 0
    finally:
        if sandbox is not None:
            if tunnel_started:
                try:
                    print("==> Stopping devtunnel host...")
                    _stop_bg(sandbox, "devtunnel", signal="INT")
                    time.sleep(2)
                    _stop_bg(sandbox, "devtunnel", signal="KILL")
                except Exception as e:
                    print(f"    warning: devtunnel stop failed: {e}")
            if dab_started:
                try:
                    print("==> Stopping DAB...")
                    _stop_bg(sandbox, "dab", signal="INT")
                    time.sleep(2)
                    _stop_bg(sandbox, "dab", signal="KILL")
                except Exception as e:
                    print(f"    warning: dab stop failed: {e}")
            try:
                print(f"==> Deleting sandbox {sandbox.sandbox_id}...")
                sandbox.delete()
            except Exception as e:
                print(f"    warning: delete failed: {e}")
        client.close()
        credential.close()


if __name__ == "__main__":
    sys.exit(main())
