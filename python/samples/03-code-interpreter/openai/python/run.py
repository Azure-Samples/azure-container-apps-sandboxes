"""LLM code interpreter — Azure OpenAI driving Python in an ACA sandbox.

The same multi-turn loop OpenAI's Code Interpreter ships, but:

  - **Your sandbox**, your egress policy, your isolation guarantees.
  - **Your data never leaves your tenant**: the CSV is uploaded into a
    fresh ACA microVM that you control; nothing about it is sent to the
    model except the snippets the model itself asks the code to print.
  - **Any Python package**: ``pip install`` whatever you want — no
    allowlist, no vendor sandbox restrictions.
  - **Per-session isolation**: every script run boots a brand-new VM;
    when ``main()`` returns the VM is gone.

Flow:

  1. Boot a ``python-3.14`` sandbox.
  2. Stage ``data/`` as ``/workspace/data/`` inside the sandbox.
  3. ``pip install pandas matplotlib`` (one-time per sandbox).
  4. Enter a chat loop with Azure OpenAI. The model has three tools:
        - ``python_exec(code)``         run Python in the sandbox
        - ``read_file(path)``           read a text file from the sandbox
        - ``download_artifact(path)``   pull a binary (e.g. plot PNG)
          back to the host's ``out/`` directory
  5. Print the final answer.

Reads configuration from ``samples/.env``:

  ACA_*                          (provisioned by setup.py / setup.sh)
  AZURE_OPENAI_ENDPOINT          required
  AZURE_OPENAI_DEPLOYMENT        required (chat-completions model)
  AZURE_OPENAI_API_VERSION       optional (default: 2024-10-21)
  AZURE_OPENAI_API_KEY           optional — falls back to AAD via
                                 DefaultAzureCredential + the
                                 'Cognitive Services OpenAI User' role.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.containerapps.sandbox import (
    SandboxGroupClient,
    endpoint_for_region,
)

# Make unicode prints (→, π, ≈, ●) work on Windows cp1252 terminals.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

SCENARIO_DIR = Path(__file__).resolve().parent
DATA_DIR = SCENARIO_DIR / "data"
OUT_DIR = SCENARIO_DIR / "out"

SANDBOX_DISK = "python-3.14"
WORKSPACE = "/workspace"
DATA_SUBDIR = "data"

DEFAULT_PROMPT = (
    "I've put a sales CSV at /workspace/data/sales.csv. "
    "Inspect it, then answer: which channel had the highest growth rate from "
    "Q1 to Q4 2024, and what is the correlation between marketing spend and "
    "revenue per channel? Also save a matplotlib chart showing monthly revenue "
    "per channel to /workspace/out/revenue.png."
)

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a data analyst running inside an isolated Azure Container Apps sandbox.

    You have three tools to work with the sandbox:
      - python_exec(code)         Run Python 3 code. stdout, stderr and exit
                                  code come back to you. The interpreter is a
                                  fresh process for each call — use globals via
                                  files if you need to persist state.
      - read_file(path)           Read a UTF-8 text file from the sandbox.
      - download_artifact(path)   Copy a binary file (e.g. a PNG you saved)
                                  from the sandbox back to the host so the
                                  caller can inspect it after this run.

    pandas, numpy and matplotlib are pre-installed. Save any plots under
    /workspace/out/ (mkdir -p first if needed) and call download_artifact on
    them so the user can view them locally.

    Be concise. Run small probes first (e.g. df.head(), df.dtypes) before any
    heavy analysis. Don't print whole DataFrames — print summaries.
""")


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it."""
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break
    if not os.environ.get("ACA_SANDBOXGROUP_REGION"):
        sys.exit(
            "error: samples/.env is missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


def _build_aoai_client():
    """Return (client, deployment) honouring API-key first then AAD."""
    from openai import AzureOpenAI  # local import keeps cold-start small

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip() or None

    missing = [n for n, v in (("AZURE_OPENAI_ENDPOINT", endpoint),
                              ("AZURE_OPENAI_DEPLOYMENT", deployment)) if not v]
    if missing:
        sys.exit(
            "error: missing required environment variables: " + ", ".join(missing) +
            "\n       Add them to samples/.env (or set them directly).\n"
            "       See: samples/sandboxes/scenarios/03-code-interpreter/openai/README.md"
        )

    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint, api_version=api_version, api_key=api_key,
        )
    else:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        client = AzureOpenAI(
            azure_endpoint=endpoint, api_version=api_version,
            azure_ad_token_provider=token_provider,
        )
    return client, deployment


# ---- Tool schema (chat-completions function-calling format) ---------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Run Python 3 code inside the sandbox. Returns stdout, stderr "
                "and the exit code. Each call is a fresh interpreter process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to run. Use print() to surface results.",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path inside the sandbox."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_artifact",
            "description": (
                "Copy a binary file from the sandbox back to the host's ./out/ "
                "directory so the user can open it locally after the run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path inside the sandbox."}
                },
                "required": ["path"],
            },
        },
    },
]


# ---- Tool implementations -------------------------------------------------

def _truncate(s: str, limit: int = 4000) -> str:
    if len(s) <= limit:
        return s
    head = s[: limit // 2]
    tail = s[-limit // 2 :]
    return f"{head}\n... [truncated {len(s) - limit} bytes] ...\n{tail}"


def tool_python_exec(sandbox, args: dict[str, Any]) -> str:
    code = args.get("code", "")
    if not isinstance(code, str) or not code.strip():
        return json.dumps({"error": "empty 'code' argument"})

    # Write the code to a file then run it — keeps multi-line / quoting safe.
    script_path = f"/tmp/cell-{uuid.uuid4().hex[:8]}.py"
    sandbox.write_file(script_path, code.encode("utf-8"))
    result = sandbox.exec(f"python3 {script_path}")
    sandbox.exec(f"rm -f {script_path}")
    return json.dumps({
        "exit_code": result.exit_code,
        "stdout": _truncate(result.stdout or ""),
        "stderr": _truncate(result.stderr or ""),
    })


def tool_read_file(sandbox, args: dict[str, Any]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path:
        return json.dumps({"error": "missing 'path'"})
    try:
        data = sandbox.read_file(path)
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
        return json.dumps({"path": path, "content": _truncate(text)})
    except Exception as exc:  # noqa: BLE001 — surface to model
        return json.dumps({"path": path, "error": str(exc)})


def tool_download_artifact(sandbox, args: dict[str, Any]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path:
        return json.dumps({"error": "missing 'path'"})
    try:
        data = sandbox.read_file(path)
        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode("utf-8")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = OUT_DIR / Path(path).name
        dest.write_bytes(bytes(data))
        return json.dumps({
            "saved_to": str(dest),
            "bytes": len(data),
            "sha256_prefix": base64.b16encode(bytes(data)[:8]).decode().lower(),
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"path": path, "error": str(exc)})


TOOL_DISPATCH = {
    "python_exec": tool_python_exec,
    "read_file": tool_read_file,
    "download_artifact": tool_download_artifact,
}


# ---- Sandbox bootstrap ----------------------------------------------------

def _stage_data(sandbox) -> None:
    """Upload every file in data/ into /workspace/data/ inside the sandbox."""
    sandbox.exec(f"mkdir -p {WORKSPACE}/{DATA_SUBDIR} {WORKSPACE}/out")
    files = sorted(p for p in DATA_DIR.glob("*") if p.is_file())
    if not files:
        return
    text_suffixes = {".csv", ".txt", ".json", ".jsonl", ".md", ".tsv", ".yaml", ".yml"}
    for f in files:
        rel = f.relative_to(DATA_DIR)
        dest = f"{WORKSPACE}/{DATA_SUBDIR}/{rel.as_posix()}"
        data = f.read_bytes()
        # Normalise line endings for text files so a CRLF host doesn't
        # leave \r\n inside a sandbox where downstream code (e.g.
        # pd.read_csv) sees CR-decorated cell values.
        if f.suffix.lower() in text_suffixes:
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        sandbox.write_file(dest, data)
        print(f"    staged {rel} ({len(data):,} bytes)")


def _install_libs(sandbox) -> None:
    print("==> Installing pandas + matplotlib in the sandbox...")
    cmd = (
        "pip install --quiet --disable-pip-version-check --break-system-packages "
        "pandas matplotlib"
    )
    r = sandbox.exec(cmd)
    if r.exit_code != 0:
        raise RuntimeError(
            f"pip install failed (exit={r.exit_code}):\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )


# ---- Main loop ------------------------------------------------------------

def run(prompt: str, *, max_turns: int = 20, model_override: str | None = None) -> int:
    _load_env()
    client, deployment = _build_aoai_client()
    if model_override:
        deployment = model_override

    region = os.environ["ACA_SANDBOXGROUP_REGION"]
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["ACA_RESOURCE_GROUP"]
    sandbox_group = os.environ["ACA_SANDBOX_GROUP"]
    run_id = uuid.uuid4().hex[:8]

    print("=" * 72)
    print("CODE INTERPRETER — Azure OpenAI in an ACA sandbox")
    print("=" * 72)
    print(f"==> deployment    : {deployment}")
    print(f"==> sandbox group : {sandbox_group} ({region})")
    print(f"==> run id        : {run_id}")
    print(f"==> prompt        : {prompt}")
    print()

    cred = DefaultAzureCredential()
    group_client = SandboxGroupClient(
        endpoint_for_region(region), cred,
        subscription_id=subscription,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    sandbox = None
    t0 = time.perf_counter()
    try:
        print(f"==> Booting sandbox (disk={SANDBOX_DISK})...")
        sandbox = group_client.begin_create_sandbox(
            disk=SANDBOX_DISK,
            labels={
                "scenario": "code-interpreter",
                "provider": "openai",
                "run": run_id,
            },
        ).result()
        print(f"    sandbox: {sandbox.sandbox_id}")

        print("==> Staging data/ into /workspace/data/ ...")
        _stage_data(sandbox)
        _install_libs(sandbox)
        print()

        # Conversation state
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        final_text: str | None = None
        for turn in range(1, max_turns + 1):
            print(f"==> Turn {turn}: model thinking...")
            resp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg = choice.message

            # Persist the assistant turn (incl. any tool_calls) for history.
            assistant_entry: dict[str, Any] = {"role": "assistant"}
            if msg.content:
                assistant_entry["content"] = msg.content
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_entry)

            if not msg.tool_calls:
                final_text = msg.content or ""
                break

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                handler = TOOL_DISPATCH.get(fn_name)
                if handler is None:
                    tool_result = json.dumps({"error": f"unknown tool {fn_name!r}"})
                else:
                    print(f"    -> {fn_name}({_short_args(fn_name, args)})")
                    tool_result = handler(sandbox, args)
                _print_tool_result(fn_name, tool_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
        else:
            final_text = (
                f"(reached max_turns={max_turns} without a final answer; "
                "last assistant message had tool calls)"
            )

        elapsed = time.perf_counter() - t0
        print()
        print("=" * 72)
        print("ANSWER")
        print("=" * 72)
        print(final_text or "(no text content)")
        print()
        if OUT_DIR.exists():
            artifacts = sorted(OUT_DIR.glob("*"))
            if artifacts:
                print(f"Saved {len(artifacts)} artifact(s) to {OUT_DIR}:")
                for a in artifacts:
                    print(f"  - {a.relative_to(SCENARIO_DIR)} ({a.stat().st_size:,} bytes)")
        print()
        print(f"(total: {elapsed:.1f}s across {turn} turn(s))")
        return 0
    finally:
        if sandbox is not None:
            try:
                print(f"==> Deleting sandbox {sandbox.sandbox_id}...")
                sandbox.delete()
            except Exception as exc:  # noqa: BLE001
                print(f"    warning: delete failed: {exc}")
        group_client.close()
        cred.close()


def _short_args(name: str, args: dict[str, Any]) -> str:
    if name == "python_exec":
        code = args.get("code", "")
        first_line = code.strip().splitlines()[0] if code.strip() else ""
        return f"code={first_line[:60]!r}{'...' if len(code) > 60 else ''}"
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _print_tool_result(name: str, raw: str) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"       [{name}] <unparseable result, {len(raw)} bytes>")
        return
    if name == "python_exec":
        out = (parsed.get("stdout") or "").strip()
        err = (parsed.get("stderr") or "").strip()
        rc = parsed.get("exit_code", "?")
        if out:
            for line in out.splitlines()[:12]:
                print(f"       │ {line}")
            extra = len(out.splitlines()) - 12
            if extra > 0:
                print(f"       │ ... ({extra} more lines)")
        if err:
            print(f"       ! stderr: {err.splitlines()[0]}")
        print(f"       (exit={rc})")
    elif name == "read_file":
        content = parsed.get("content")
        if content is None:
            print(f"       [read_file] {parsed.get('error', '?')}")
        else:
            lines = content.splitlines()[:5]
            for line in lines:
                print(f"       │ {line}")
            if len(content.splitlines()) > 5:
                print(f"       │ ... ({len(content.splitlines()) - 5} more lines)")
    elif name == "download_artifact":
        if parsed.get("saved_to"):
            print(f"       saved -> {parsed['saved_to']} ({parsed.get('bytes', 0):,} bytes)")
        else:
            print(f"       error: {parsed.get('error', '?')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="LLM code interpreter — Azure OpenAI in an ACA sandbox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "prompt", nargs="?", default=DEFAULT_PROMPT,
        help="The data-analysis question to answer.",
    )
    parser.add_argument(
        "--model", help="Override AZURE_OPENAI_DEPLOYMENT for this run.",
    )
    parser.add_argument(
        "--max-turns", type=int, default=20,
        help="Stop after this many model-tool turns (default: 20).",
    )
    args = parser.parse_args(argv)
    return run(args.prompt, max_turns=args.max_turns, model_override=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
