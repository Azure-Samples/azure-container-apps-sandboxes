"""host_listener — FastAPI app running inside the host sandbox.

The Connector Namespace's "When a file is created (properties only)"
trigger POSTs directly to this listener (via the ADC proxy URL
``https://<sandboxId>--8080.<region>.adcproxy.io``). The proxy has
already validated that the caller is the namespace MI; we trust the
trust boundary at the proxy and just process the payload.

Per request (one SharePoint file event):

  1. Receive the SharePoint file's ``dynamicProperties`` block in
     the request body. The trigger config's body template is
     ``@triggerBody()`` so we get the full property bag (ID,
     FileLeafRef, FileRef, ``{Identifier}``, Editor, etc.).
  2. Apply the self-loop guard — skip anything in the output
     folder or with a ``.json`` extension, since the namespace also
     fires for files we ourselves upload to /Extracted.
  3. Allocate a fresh per-run workspace at ``/work/<run-id>/``.
  4. Build a prompt that walks Copilot CLI through the SharePoint
     MCP tools step-by-step (see ``prompt.md``):
       a. ``getSiteByPath`` → siteId
       b. ``listDocumentLibrariesInSite`` → documentLibraryId
       c. ``getFolderChildren`` → match FileLeafRef → fileId
       d. ``readSmallBinaryFile`` → bytes (base64)
       e. extract via ``pdftotext`` / ``tesseract`` / fresh Python
       f. ``createSmallTextFile`` → upload result JSON to /Extracted
  5. Run Copilot CLI non-interactively with the prompt.
  6. Return 200 to the namespace. Long-running work happens in a
     background task; we ack fast so the namespace doesn't retry.

Egress: the sandbox's egress policy (applied at boot by the
post-deploy script) is deny-default + Transform rules that stamp
X-API-Key on outbound MCP calls + Authorization on GitHub Copilot
hosts. The sandbox itself holds NO MCP api key.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("listener")

# ---- Configuration (populated by bootstrap.sh via env) ----------------------

# The runtime URL of the SharePoint MCP server on the namespace. Set
# at sandbox-bootstrap time by the post-deploy script (which reads it
# from the mcpserverConfig data plane after the namespace is provisioned).
SHAREPOINT_MCP_URL = os.environ["SHAREPOINT_MCP_URL"]

# The SharePoint document library / folder where extracted result
# JSONs should land. Pinned into the prompt so Copilot doesn't have
# to guess. Both passed in as plain env vars at bootstrap time.
SHAREPOINT_SITE_URL = os.environ.get("SHAREPOINT_SITE_URL", "").strip()
SHAREPOINT_LIBRARY_ID = os.environ.get("SHAREPOINT_LIBRARY_ID", "").strip()

# Input folder: only files whose path is inside this folder will be
# processed. The trigger fires for everything in the library (the
# `GetOnNewFileItems` operation has no folder filter; the
# folder-scoped operations OnNewFile / OnUpdatedFile are deprecated).
# Setting this to "" disables the input filter (process all files).
SHAREPOINT_INPUT_FOLDER = os.environ.get("SHAREPOINT_INPUT_FOLDER", "").strip().strip("/")

# Output folder: results go here. Self-loop guard also keys off this
# so we don't re-process the JSONs we ourselves upload.
SHAREPOINT_OUTPUT_FOLDER = os.environ.get("SHAREPOINT_OUTPUT_FOLDER", "Extracted").strip().strip("/")

# Copilot CLI MUST have a GitHub credential in its env before it
# attempts any network call (see scenario 10 notes — its auth error
# fires before the egress proxy can intervene). Provided by the
# bootstrap script as a sandbox env var (NOT a sandbox secret —
# sandboxes don't currently expose secret refs, only env). The egress
# proxy ALSO has a Transform rule stamping Authorization on api.github.com
# and the two githubcopilot.com hosts as defense-in-depth.
COPILOT_GITHUB_TOKEN = os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()

PROMPT_TEMPLATE = Path(__file__).with_name("prompt.md").read_text(encoding="utf-8")

app = FastAPI(title="sandboxes-connectors-document-automation listener")

# Background tasks. Same pattern as scenario 10's receiver — we ack
# the namespace fast so it doesn't retry, then do the long-running work
# in a task. Keep a set so the asyncio task isn't GC'd prematurely.
_inflight: set[asyncio.Task[Any]] = set()


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "sandboxes-connectors-document-automation",
        "status": "ok",
        "sharepoint_mcp_configured": bool(SHAREPOINT_MCP_URL),
    }


@app.get("/healthz")
def healthz() -> Response:
    # Used by the bootstrap script to wait for uvicorn to come up
    # before declaring the sandbox ready for trigger registration.
    return Response(status_code=200)


@app.post("/")
@app.post("/trigger")
async def trigger(request: Request) -> dict[str, Any]:
    """Entry point for the Connector Namespace trigger.

    The trigger config's `notificationDetails.body` is
    ``@{triggerBody()?['dynamicProperties']}``, so the body here is
    just the SharePoint file's `dynamicProperties` block — typically
    something like::

        {
          "ID": 42,
          "FileLeafRef": "invoice-2026-001.pdf",
          "FileRef": "/teams/Finance/Shared Documents/invoice-2026-001.pdf",
          "{Identifier}": "%252fteams%252f...%252finvoice-2026-001.pdf",
          ...
        }

    Different SharePoint libraries / trigger filters may shape this
    slightly differently. We pass the whole blob to Copilot in the
    prompt so the model can navigate it.
    """
    # Always log the raw body + content-type for diagnostics. The
    # namespace sometimes sends a slightly different shape than we
    # expect (e.g., wrapped in `triggerBody()` envelope, or empty
    # for keep-alive pokes).
    raw = await request.body()
    ctype = request.headers.get("content-type", "<none>")
    log.info("trigger POST: content-type=%s len=%d", ctype, len(raw))
    if not raw:
        log.warning("trigger POST: empty body — likely a namespace probe; acking 200")
        return {"accepted": "empty"}
    preview = raw[:1500].decode("utf-8", "replace")
    log.info("trigger POST body preview:\n%s", preview)
    try:
        import json as _json
        payload = _json.loads(raw)
    except Exception as exc:
        log.warning("trigger POST: JSON parse failed (%s); acking 200 so the namespace doesn't retry", exc)
        return {"accepted": "non-json"}

    run_id = uuid.uuid4().hex[:8]
    log.info("[%s] trigger received: top-level keys=%s", run_id, sorted(payload.keys())[:12] if isinstance(payload, dict) else f"<{type(payload).__name__}>")

    task = asyncio.create_task(_process_one(payload, run_id))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)

    return {"accepted": run_id}


async def _process_one(file_props: dict[str, Any], run_id: str) -> None:
    """Run Copilot CLI against this one file's properties.

    Workspace is /work/<run-id>/ so concurrent runs (if the namespace
    delivers a batch) don't trample each other. The agent is told to
    confine its file I/O to that workspace.
    """
    file_ref = str(file_props.get("FileRef", "") or "")
    identifier = str(file_props.get("{Identifier}", "") or "")
    leaf = str(file_props.get("FileLeafRef", "") or "")

    # Self-loop guard: the namespace fires for the JSONs WE upload to
    # the output folder. Skip anything whose path is inside the output
    # folder or whose name ends with `.json`.
    out_folder = (SHAREPOINT_OUTPUT_FOLDER or "Extracted").strip("/")
    is_own_output = (
        f"/{out_folder}/" in file_ref
        or f"%252f{out_folder.replace('/', '%252f')}%252f" in identifier
        or f"%2f{out_folder.replace('/', '%2f')}%2f" in identifier
        or leaf.lower().endswith(".json")
    )
    if is_own_output:
        log.info(
            "[%s] skipping (own output / in %s folder): leaf=%r ref=%r",
            run_id, out_folder, leaf, file_ref[:120],
        )
        return

    # Input-folder filter: when set, only process files whose path is
    # inside this folder. The trigger has no folder param so we filter
    # post-hoc here. Empty/unset = process everything in the library.
    if SHAREPOINT_INPUT_FOLDER:
        in_folder = SHAREPOINT_INPUT_FOLDER.strip("/")
        in_match = (
            f"/{in_folder}/" in file_ref
            or f"%252f{in_folder.replace('/', '%252f')}%252f" in identifier
            or f"%2f{in_folder.replace('/', '%2f')}%2f" in identifier
        )
        if not in_match:
            log.info(
                "[%s] skipping (not in input folder %s): leaf=%r ref=%r",
                run_id, in_folder, leaf, file_ref[:120],
            )
            return

    workspace = Path("/work") / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        prompt = _render_prompt(file_props, run_id, workspace)
        (workspace / "prompt.md").write_text(prompt, encoding="utf-8")
        _write_mcp_config(workspace)
        await _run_copilot(workspace, run_id)
        log.info("[%s] done", run_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("[%s] processing failed: %s", run_id, exc)
    finally:
        pass


def _render_prompt(file_props: dict[str, Any], run_id: str, workspace: Path) -> str:
    sharepoint_target = ""
    if SHAREPOINT_SITE_URL:
        from urllib.parse import urlparse
        u = urlparse(SHAREPOINT_SITE_URL)
        host = u.netloc
        srv_path = u.path.lstrip("/")
        sharepoint_target = (
            "\n"
            f"- site URL:        {SHAREPOINT_SITE_URL}\n"
            f"  hostname:        {host}\n"
            f"  serverRelative:  {srv_path}\n"
        )
        if SHAREPOINT_LIBRARY_ID:
            sharepoint_target += (
                f"- library list ID (from trigger): {SHAREPOINT_LIBRARY_ID}\n"
                "  (this is the SharePoint LIST id; the MCP tools want the\n"
                "   DRIVE id which you get from listDocumentLibrariesInSite — pick\n"
                "   whichever document library matches your scenario, typically\n"
                "   the first or only one in the site if this site has just one.)\n"
            )
        if SHAREPOINT_INPUT_FOLDER:
            sharepoint_target += (
                f"- input folder (the file you're processing is inside): {SHAREPOINT_INPUT_FOLDER}\n"
            )
        sharepoint_target += (
            f"- output folder (where to upload your result):           {SHAREPOINT_OUTPUT_FOLDER}\n"
        )
    file_props_pretty = _safe_pretty(file_props, max_chars=4000)
    return PROMPT_TEMPLATE.format(
        run_id=run_id,
        workspace=str(workspace),
        file_props=file_props_pretty,
        sharepoint_target=sharepoint_target,
    )


def _write_mcp_config(workspace: Path) -> None:
    # Copilot CLI v1.x reads MCP server config from `./.mcp.json`
    # (workspace-level) and `~/.copilot/mcp-config.json` (user-level).
    # We write the workspace-level file to keep each run self-contained.
    # Top-level key is `mcpServers` (camelCase). The egress proxy adds
    # X-API-Key on the way out so the URL here is the bare namespace URL.
    mcp_json = (
        '{\n'
        '  "mcpServers": {\n'
        '    "sharepoint": {\n'
        '      "type": "http",\n'
        f'      "url": "{SHAREPOINT_MCP_URL}"\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    (workspace / ".mcp.json").write_text(mcp_json, encoding="utf-8")


def _safe_pretty(obj: Any, *, max_chars: int) -> str:
    import json
    try:
        s = json.dumps(obj, indent=2, default=str)
    except Exception:
        s = repr(obj)
    if len(s) > max_chars:
        s = s[:max_chars] + "\n... (truncated)"
    return s


async def _run_copilot(workspace: Path, run_id: str) -> None:
    log.info("[%s] running copilot in %s", run_id, workspace)
    env = os.environ.copy()
    if COPILOT_GITHUB_TOKEN:
        env["COPILOT_GITHUB_TOKEN"] = COPILOT_GITHUB_TOKEN

    # Diagnostic — log copilot version + the MCP server registration
    # before the real run, like scenario 10 does.
    for cmd in ("copilot --version", "copilot mcp list"):
        try:
            r = await _run("/bin/bash", "-lc", cmd, cwd=workspace, env=env, timeout=15)
            log.info("[%s] %s: %s", run_id, cmd, (r.stdout or "").strip()[:500])
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] %s failed: %s", run_id, cmd, exc)

    prompt_path = workspace / "prompt.md"
    r = await _run(
        "/bin/bash", "-lc",
        f'copilot --allow-all-tools -p "$(cat {prompt_path})" 2>&1',
        cwd=workspace, env=env, timeout=360,
    )
    log.info(
        "[%s] copilot exit=%d\nstdout:\n%s\n[--- end stdout ---]",
        run_id, r.returncode, (r.stdout or ""),
    )
    if r.returncode != 0:
        raise RuntimeError(f"copilot run failed exit={r.returncode}")


class _ProcResult:
    __slots__ = ("returncode", "stdout", "stderr")
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _run(
    *cmd: str, cwd: Path, env: dict[str, str], timeout: float,
) -> _ProcResult:
    """Run a subprocess with a timeout. Captures stdout+stderr together."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"command {cmd!r} exceeded timeout={timeout}s")
    return _ProcResult(proc.returncode or 0, out_b.decode("utf-8", "replace"), "")


# Local dev entrypoint
if __name__ == "__main__":
    import uvicorn  # noqa: E402

    uvicorn.run(app, host="0.0.0.0", port=8080)
