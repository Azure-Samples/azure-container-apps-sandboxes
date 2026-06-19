"""Autonomous Swarm supervisor — runs INSIDE the orchestrator sandbox.

Uses the sandbox group's SystemAssigned Managed Identity for both:
  - Azure OpenAI (Cognitive Services OpenAI User role)
  - The worker sandbox group's data plane (Container Apps SandboxGroup Data Owner)

No API keys are accepted; we assert this on startup as a guard against
accidental regressions in the launcher.

Reads its configuration from a JSON file path passed as argv[1] (written
by launcher.py via sandbox.write_file).

Emits a single `RESULT={...json...}` line on success that the host parses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Pin TLS trust store to the sandbox's SYSTEM CA bundle BEFORE any HTTP lib
# is imported. The ACA sandbox runtime transparently proxies outbound HTTPS
# through an "ADC Egress Proxy" that re-signs server certs with its own CA.
# That proxy CA is installed into the base image's system trust store
# (/etc/ssl/certs/ca-certificates.crt) but NOT into certifi's bundle, so any
# library that pins certifi (httpx, openai, etc.) will fail SSL verification.
# Setting SSL_CERT_FILE + REQUESTS_CA_BUNDLE + CURL_CA_BUNDLE handles most
# stdlib + requests + aiohttp paths. httpx/openai still need an explicit
# `verify=` arg, which we pass at construction sites below.
# ---------------------------------------------------------------------------
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
if os.path.exists(SYSTEM_CA_BUNDLE):
    os.environ.setdefault("SSL_CERT_FILE", SYSTEM_CA_BUNDLE)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", SYSTEM_CA_BUNDLE)
    os.environ.setdefault("CURL_CA_BUNDLE", SYSTEM_CA_BUNDLE)

# ---------------------------------------------------------------------------
# Zero-secret invariant — fail fast if the launcher regresses
# ---------------------------------------------------------------------------

FORBIDDEN_ENV = (
    "AZURE_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_USERNAME",
    "AZURE_PASSWORD",
    "AZURE_FEDERATED_TOKEN_FILE",
)


def assert_zero_secret() -> None:
    leaks = [k for k in FORBIDDEN_ENV if os.environ.get(k)]
    if leaks:
        print(
            "FATAL: zero-secret invariant violated. Forbidden env vars present: "
            + ", ".join(leaks),
            file=sys.stderr,
        )
        sys.exit(2)
    # No host credentials directory should have been mounted in.
    if Path.home().joinpath(".azure").exists():
        print(
            "FATAL: zero-secret invariant violated. ~/.azure exists in sandbox.",
            file=sys.stderr,
        )
        sys.exit(2)
    print("[zero-secret] OK — no API keys, app secrets, or user creds in env.")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def parse_config() -> dict:
    p = argparse.ArgumentParser()
    p.add_argument("config_path", help="Path to JSON config (written by launcher).")
    args = p.parse_args()
    cfg = json.loads(Path(args.config_path).read_text(encoding="utf-8"))
    required = ("run_id", "mode", "task", "workers", "aoai", "worker_group")
    missing = [k for k in required if k not in cfg]
    if missing:
        print(f"FATAL: config missing keys: {missing}", file=sys.stderr)
        sys.exit(2)
    return cfg


# ---------------------------------------------------------------------------
# In-sandbox AOAI RBAC retry
# ---------------------------------------------------------------------------


async def wait_for_aoai_rbac(
    endpoint: str, api_version: str, token_provider, timeout: float = 300.0
) -> None:
    """Poll `GET /openai/models` until 200 (RBAC propagated) or timeout."""
    import httpx

    url = f"{endpoint.rstrip('/')}/openai/models?api-version={api_version}"
    start = time.monotonic()
    attempt = 0
    last_status: Any = None
    last_body: str = ""
    backoff = 2.0
    # Use the sandbox's SYSTEM CA bundle so the ADC egress proxy's cert
    # verifies. certifi alone is NOT enough — proxy CA is not in certifi.
    verify_arg = SYSTEM_CA_BUNDLE if os.path.exists(SYSTEM_CA_BUNDLE) else True
    async with httpx.AsyncClient(timeout=30.0, verify=verify_arg) as client:
        while time.monotonic() - start < timeout:
            attempt += 1
            try:
                tok = await token_provider()
                r = await client.get(url, headers={"Authorization": f"Bearer {tok}"})
                if r.status_code == 200:
                    n = len((r.json() or {}).get("data", []))
                    print(f"[rbac] AOAI ready after {attempt} attempt(s) "
                          f"({time.monotonic()-start:.1f}s) — {n} models visible")
                    return
                last_status = r.status_code
                last_body = r.text[:300]
            except Exception as exc:
                last_status = type(exc).__name__
                last_body = str(exc)[:300]
            sleep_for = backoff + random.uniform(0, backoff * 0.3)
            print(f"[rbac] attempt {attempt}: {last_status} — sleeping {sleep_for:.1f}s")
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 1.8, 45.0)
    raise RuntimeError(
        f"AOAI RBAC did not propagate within {timeout:.0f}s. "
        f"Last status={last_status}, body={last_body!r}"
    )


# ---------------------------------------------------------------------------
# Decomposition + synthesis prompts
# ---------------------------------------------------------------------------


DECOMPOSE_PROMPT = """\
You are a research planner. Decompose the following user task into exactly {n} \
non-overlapping subtasks, each suitable for an isolated worker agent with \
shell + filesystem access in a fresh Linux sandbox.

User task:
{task}

Each subtask must include:
  - worker_id: integer in [1, {n}]
  - title: 2-6 word headline
  - prompt: a self-contained instruction to the worker (3-8 sentences). Include \
    any specific files to read, commands to run, or topics to cover. The worker \
    will not see other subtasks or the original user task.

Return ONLY a JSON object of shape {{"subtasks": [...]}}. No prose.
"""


SYNTHESIZE_PROMPT = """\
You are a research lead. Given the original user task and N worker findings, \
synthesize one cohesive final answer. Mention any workers that failed (and why \
briefly), but DO NOT fabricate findings. Be concise but specific.

Original task:
{task}

Worker findings:
{findings}
"""


WORKER_INSTRUCTIONS = """\
You are an autonomous research worker running in an isolated Linux sandbox \
with Shell and Filesystem capabilities. Use your tools to explore, read \
files, and run commands necessary to answer the prompt. When you have a \
confident answer, return a 2-4 paragraph summary. Do not invent facts you \
have not verified by reading files or running commands. If you cannot \
complete the task, say so plainly with the reason.
"""


# ---------------------------------------------------------------------------
# Smoke mode
# ---------------------------------------------------------------------------


async def smoke_run(cfg: dict, cred, model_token_provider) -> None:
    """Confirm: AOAI /models 200 + worker group list_sandboxes succeeds."""
    print(f"[smoke] AOAI auth was already validated above")

    print(f"[smoke] Listing worker group sandboxes (proves MI works against worker scope)...")
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.containerapps.sandbox import endpoint_for_region

    wg = cfg["worker_group"]
    client = SandboxGroupClient(
        endpoint_for_region(wg["region"]), cred,
        subscription_id=wg["subscription_id"],
        resource_group=wg["resource_group"],
        sandbox_group=wg["sandbox_group"],
    )
    try:
        count = 0
        async for _ in client.list_sandboxes():
            count += 1
        print(f"[smoke] worker group has {count} pre-existing sandbox(es) (0 is normal)")
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Full-mode helpers
# ---------------------------------------------------------------------------


async def decompose_task(model, task: str, n: int) -> list[dict]:
    """Ask the LLM for exactly n subtasks, retry once on shape errors."""
    from agents import Agent, Runner

    prompt = DECOMPOSE_PROMPT.format(task=task, n=n)
    agent = Agent(name="decomposer", instructions="You return strict JSON only.", model=model)

    for attempt in (1, 2):
        try:
            result = await Runner.run(agent, prompt, max_turns=2)
            text = (result.final_output or "").strip()
            # Defensive: strip ```json fences if the model added them
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
                text = text.strip()
            data = json.loads(text)
            subs = data.get("subtasks") or []
            if isinstance(subs, list) and len(subs) == n and all(
                isinstance(s, dict) and "worker_id" in s and "prompt" in s for s in subs
            ):
                return subs
            raise ValueError(
                f"unexpected shape: got {len(subs)} subtasks, expected {n}"
            )
        except Exception as exc:
            print(f"[decompose] attempt {attempt} failed: {exc}")
            if attempt == 2:
                # Fallback: deterministic naive split.
                print("[decompose] falling back to deterministic split")
                return [
                    {
                        "worker_id": i + 1,
                        "title": f"Aspect {i + 1}",
                        "prompt": f"For the task below, focus on aspect #{i + 1} of {n}. "
                                  f"Aspects should be distinct sub-questions you can answer "
                                  f"by reading files and running commands.\n\nTask: {task}",
                    }
                    for i in range(n)
                ]
    return []  # unreachable


async def run_worker(
    subtask: dict, model, aca_client, group_info: dict, run_id: str
) -> dict:
    """Run a single SandboxAgent on a fresh worker sandbox; return findings dict."""
    from agents import Runner
    from agents.run_config import RunConfig
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Shell, Filesystem

    from agents_aca_sandboxes import ACASandboxesClientOptions

    wid = subtask["worker_id"]
    agent = SandboxAgent(
        name=f"worker-{wid}",
        instructions=WORKER_INSTRUCTIONS,
        capabilities=[Shell(), Filesystem()],
        model=model,
    )
    options = ACASandboxesClientOptions(
        disk="ubuntu",
        labels={
            "scenario": "08-sandbox-agents",
            "demo": "03-autonomous-swarm",
            "role": "worker",
            "run-id": run_id,
            "worker-id": str(wid),
        },
        auto_suspend_seconds=300,
    )

    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                subtask["prompt"],
                max_turns=20,
                run_config=RunConfig(
                    sandbox=SandboxRunConfig(
                        client=aca_client, options=options, manifest={}
                    ),
                ),
            ),
            timeout=300.0,
        )
        return {
            "worker_id": wid,
            "title": subtask.get("title", ""),
            "ok": True,
            "summary": (result.final_output or "").strip(),
        }
    except asyncio.TimeoutError:
        return {"worker_id": wid, "title": subtask.get("title", ""), "ok": False, "error": "timeout"}
    except Exception as exc:
        return {
            "worker_id": wid,
            "title": subtask.get("title", ""),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def synthesize(model, task: str, worker_results: list[dict]) -> str:
    from agents import Agent, Runner

    findings_text = "\n\n".join(
        f"--- Worker {w['worker_id']} ({w.get('title','')}) ---\n"
        + (w["summary"] if w.get("ok") else f"FAILED: {w.get('error','unknown')}")
        for w in sorted(worker_results, key=lambda w: w["worker_id"])
    )
    prompt = SYNTHESIZE_PROMPT.format(task=task, findings=findings_text)
    agent = Agent(name="synthesizer", instructions="You write concise expert summaries.", model=model)
    result = await Runner.run(agent, prompt, max_turns=2)
    return (result.final_output or "").strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async() -> int:
    cfg = parse_config()
    assert_zero_secret()

    # Disable tracing — we have no OpenAI API key and don't want the SDK to try.
    try:
        from agents import set_tracing_disabled
        set_tracing_disabled(True)
    except Exception:
        pass  # older SDK versions may not have this

    from azure.identity.aio import (
        ManagedIdentityCredential, get_bearer_token_provider,
    )
    from openai import AsyncAzureOpenAI
    from agents.models.openai_responses import OpenAIResponsesModel
    import httpx

    # Pin AsyncAzureOpenAI's underlying httpx client to the sandbox's system
    # CA bundle so the ADC egress-proxy cert verifies.
    verify_arg = SYSTEM_CA_BUNDLE if os.path.exists(SYSTEM_CA_BUNDLE) else True
    openai_http_client = httpx.AsyncClient(verify=verify_arg, timeout=180.0)

    cred = ManagedIdentityCredential()
    aoai_token_provider = get_bearer_token_provider(
        cred, "https://cognitiveservices.azure.com/.default"
    )

    aoai_cfg = cfg["aoai"]
    print(f"[init] AOAI endpoint: {aoai_cfg['endpoint']}")
    print(f"[init] Deployment:   {aoai_cfg['deployment']}")
    print(f"[init] API version:  {aoai_cfg['api_version']}")

    # Wait for AOAI RBAC propagation before any heavy work.
    await wait_for_aoai_rbac(
        aoai_cfg["endpoint"], aoai_cfg["api_version"], aoai_token_provider
    )

    openai_client = AsyncAzureOpenAI(
        azure_endpoint=aoai_cfg["endpoint"],
        api_version=aoai_cfg["api_version"],
        azure_ad_token_provider=aoai_token_provider,
        http_client=openai_http_client,
    )
    model = OpenAIResponsesModel(model=aoai_cfg["deployment"], openai_client=openai_client)

    try:
        # ---- smoke mode: validate worker-group data plane, then exit -----
        if cfg["mode"] == "smoke-run":
            await smoke_run(cfg, cred, aoai_token_provider)
            print("RESULT=" + json.dumps({"mode": "smoke-run", "ok": True}))
            return 0

        # ---- full mode --------------------------------------------------
        n = int(cfg["workers"])
        task = cfg["task"]
        run_id = cfg["run_id"]
        print(f"[plan] Decomposing task into {n} subtasks...")
        subtasks = await decompose_task(model, task, n)
        for s in subtasks:
            print(f"    [w{s['worker_id']}] {s.get('title','(no title)')}")

        # Build worker group client + ACA agents extension client.
        from azure.containerapps.sandbox.aio import SandboxGroupClient
        from azure.containerapps.sandbox import endpoint_for_region
        from agents_aca_sandboxes import ACASandboxesClient

        wg = cfg["worker_group"]
        worker_group_client = SandboxGroupClient(
            endpoint_for_region(wg["region"]), cred,
            subscription_id=wg["subscription_id"],
            resource_group=wg["resource_group"],
            sandbox_group=wg["sandbox_group"],
        )
        aca_client = ACASandboxesClient(worker_group_client)

        try:
            print(f"[run] Spawning {n} workers in parallel against {wg['sandbox_group']!r}...")
            worker_results = await asyncio.gather(
                *(
                    run_worker(s, model, aca_client, wg, run_id)
                    for s in subtasks
                ),
                return_exceptions=False,  # run_worker catches its own exceptions
            )
            ok = sum(1 for w in worker_results if w.get("ok"))
            print(f"[run] {ok}/{n} workers succeeded")

            print("[synthesize] Producing final answer...")
            final = await synthesize(model, task, worker_results)
        finally:
            await worker_group_client.close()

        payload = {
            "mode": "full",
            "run_id": run_id,
            "task": task,
            "workers": worker_results,
            "final_answer": final,
        }
        print("RESULT=" + json.dumps(payload))
        # Partial worker failures are surfaced in payload["workers"] but the
        # supervisor's job (decompose -> dispatch -> synthesize) succeeded.
        # Exit 0 so the host parses and prints the final answer.
        return 0

    finally:
        try:
            await openai_client.close()
        except Exception:
            pass
        try:
            await cred.close()
        except Exception:
            pass


SUPERVISOR_DONE_FILE = "/tmp/supervisor.done"


def main() -> int:
    rc = 1
    try:
        rc = asyncio.run(main_async())
        return rc
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
        raise
    except Exception:
        traceback.print_exc()
        rc = 1
        return rc
    finally:
        # Always signal completion to the host poller, with exit code.
        try:
            with open(SUPERVISOR_DONE_FILE, "w", encoding="utf-8") as f:
                f.write(f"{rc}\n")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
