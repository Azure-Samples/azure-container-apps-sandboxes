"""Swarm Research Agent — parallel multi-agent research.

This demo showcases parallel multi-agent research where each worker researches a
different topic in its own isolated ACA sandbox. The findings are aggregated into
a comprehensive report.

**What It Demonstrates:**
- Parallel research across multiple topics/sources
- Each agent in isolated ACA sandbox with bounded concurrency
- Real-world competitive analysis, documentation synthesis, etc.
- Git clone, web scraping, and document analysis

**Usage:**

    python swarm_research_agent.py \\
        --topic "framework-a|Framework A|https://github.com/org/repo-a|What are the key features?" \\
        --topic "framework-b|Framework B|https://github.com/org/repo-b|How does it compare?" \\
        --concurrency 2

Output lands in ``./.run-output/swarm-<uuid8>/``:
- ``final-answer.md`` — Aggregated findings across all topics
- ``findings/<topic-key>.md`` — Each worker's raw analysis  
- ``summary.json`` — Structured data for further processing

Configuration: see ``.env.example`` for Azure OpenAI and ACA sandbox group settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.model_provider import build_azure_openai_model  # noqa: E402


@dataclass(frozen=True)
class ResearchTopic:
    """A topic for one worker to research."""
    key: str  # Short identifier (lowercase-with-dashes)
    name: str  # Display name
    source: str  # GitHub repo URL or doc website
    question: str  # Research question for the agent


WORKER_INSTRUCTIONS = """\
You are a research worker analyzing a specific topic.

Task: {question}

Instructions:
1. Clone the repository: cd /workspace && git clone --depth 1 {source} repo
2. Explore: ls /workspace/repo/, find README files, docs/
3. Research thoroughly to answer the question
4. Focus on: What is it? How does it work? Key features? Use cases?

Respond with a brief summary (2-3 sentences) followed by key findings as bullet points.
"""


@dataclass
class WorkerResult:
    topic: ResearchTopic
    success: bool
    answer: str
    elapsed: float
    error: str = ""


async def research_topic(
    topic: ResearchTopic,
    sandbox_client: "ACASandboxesClient",
    sandbox_options: "ACASandboxesClientOptions",
    model: "OpenAIResponsesModel",
    semaphore: asyncio.Semaphore,
    run_id: str,
    timeout: float = 180.0,
) -> WorkerResult:
    """Research one topic in an isolated sandbox."""
    from agents import Runner
    from agents.run_config import RunConfig
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Filesystem, Shell

    async with semaphore:
        t0 = time.perf_counter()
        print(f"    📦 [{topic.key:10s}] -> provisioning sandbox...")
        try:
            async with asyncio.timeout(timeout):
                instructions = WORKER_INSTRUCTIONS.format(
                    question=topic.question,
                    source=topic.source,
                )
                agent = SandboxAgent(
                    name=f"research-{topic.key}",
                    model=model,
                    instructions=instructions,
                    capabilities=[Shell(), Filesystem()],
                )
                run_config = RunConfig(
                    sandbox=SandboxRunConfig(
                        client=sandbox_client,
                        options=sandbox_options,
                        manifest={},  # Empty — agent will git clone
                    ),
                )

                result = await Runner.run(
                    agent,
                    topic.question,
                    run_config=run_config,
                    max_turns=15,
                )
                response_text = result.final_output or ""

                elapsed = time.perf_counter() - t0
                print(f"    ✅ [{topic.key:10s}] DONE in {elapsed:5.1f}s")

                return WorkerResult(
                    topic=topic,
                    success=True,
                    answer=response_text,
                    elapsed=elapsed,
                )
        except TimeoutError:
            elapsed = time.perf_counter() - t0
            print(f"    ⏱️  [{topic.key:10s}] TIMEOUT after {elapsed:5.1f}s")
            return WorkerResult(
                topic=topic,
                success=False,
                answer="",
                elapsed=elapsed,
                error=f"Timeout after {timeout:.0f}s",
            )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"    ❌ [{topic.key:10s}] ERROR: {str(e)[:50]}")
            return WorkerResult(
                topic=topic,
                success=False,
                answer="",
                elapsed=elapsed,
                error=str(e),
            )


def parse_topic_arg(s: str) -> ResearchTopic:
    """Parse a topic from format: key|name|source|question"""
    parts = s.split("|", 3)  # Split into max 4 parts (question may contain |)
    if len(parts) != 4:
        raise ValueError(
            f"Topic must be: key|name|source|question. Got {len(parts)} parts."
        )
    key, name, source, question = parts
    # Validate key (lowercase alphanumeric + dashes)
    key = key.strip().lower()
    if not all(c.isalnum() or c == "-" for c in key):
        raise ValueError(f"Topic key must be lowercase alphanumeric+dashes. Got: {key!r}")
    return ResearchTopic(
        key=key,
        name=name.strip(),
        source=source.strip(),
        question=question.strip(),
    )


async def run_swarm(
    topics: list[ResearchTopic],
    concurrency: int,
    timeout_per_worker: float,
    run_id: str,
    output_dir: Path,
) -> int:
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.identity.aio import DefaultAzureCredential
    from agents import set_tracing_disabled
    from agents_aca_sandboxes import ACASandboxesClient, load_config

    # Disable OpenAI tracing (we use Azure OpenAI, not OpenAI platform)
    set_tracing_disabled(True)

    # Check for duplicate keys
    keys = [t.key for t in topics]
    if len(keys) != len(set(keys)):
        duplicates = [k for k in set(keys) if keys.count(k) > 1]
        raise ValueError(f"Duplicate topic keys: {', '.join(duplicates)}")

    cfg = load_config()
    model, aoai_client = build_azure_openai_model()
    
    t_start = time.perf_counter()
    
    print("=" * 72)
    print("SWARM RESEARCH AGENT — Parallel Multi-Agent Research")
    print("=" * 72)
    print(f"==> ACA sandbox group : {cfg.sandbox_group} ({cfg.region})")
    print(f"==> AOAI deployment   : {os.environ.get('AZURE_OPENAI_DEPLOYMENT')}")
    print(f"==> Run ID            : {run_id}")
    print(f"==> Topics            : {', '.join(t.key for t in topics)}")
    print(f"==> Concurrency       : {concurrency}")
    print(f"==> Timeout per worker: {timeout_per_worker:.0f}s")
    print(f"==> Output dir        : {output_dir}")
    print()
    
    # Show architecture diagram
    print("┌─────────────────────────────────────────────────────────────────────┐")
    print("│                    SWARM ARCHITECTURE                               │")
    print("├─────────────────────────────────────────────────────────────────────┤")
    print("│                                                                     │")
    print("│   Runner (local)                                                    │")
    print("│      │                                                               │")
    print("│      ├──> Topic 1 ──> [ACA Sandbox 1] ──> Agent ──> Git Clone      │")
    print("│      │                     (isolated)         │                      │")
    print("│      │                                        └──> Research & Report │")
    print("│      │                                                               │")
    print("│      ├──> Topic 2 ──> [ACA Sandbox 2] ──> Agent ──> Git Clone      │")
    print("│      │                     (isolated)         │                      │")
    print("│      │                                        └──> Research & Report │")
    print("│      │                                                               │")
    print("│      └──> Topic N ──> [ACA Sandbox N] ──> Agent ──> Git Clone      │")
    print("│                           (isolated)         │                      │")
    print("│                                              └──> Research & Report │")
    print("│                                                                     │")
    print("│   Each sandbox is a fully-isolated microVM with:                   │")
    print("│   • Ubuntu filesystem (git, bash, filesystem tools)                │")
    print("│   • No network access to host                                      │")
    print("│   • Automatic cleanup after completion                             │")
    print("│                                                                     │")
    print("└─────────────────────────────────────────────────────────────────────┘")
    print()

    output_dir.mkdir(parents=True, exist_ok=True)
    findings_dir = output_dir / "findings"
    findings_dir.mkdir(exist_ok=True)

    cred = DefaultAzureCredential()
    concurrency_sem = asyncio.Semaphore(concurrency)

    try:
        async with SandboxGroupClient(
            endpoint=cfg.endpoint,
            credential=cred,
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            sandbox_group=cfg.sandbox_group,
        ) as gc:
            sandbox_client = ACASandboxesClient(gc)
            
            # Create sandbox options for all workers
            from agents_aca_sandboxes import ACASandboxesClientOptions
            sandbox_options = ACASandboxesClientOptions(
                disk="ubuntu",
                labels={
                    "scenario": "08-sandbox-agents",
                    "demo": "swarm-research",
                    "run-id": run_id,
                    "owner": os.environ.get("USERNAME", "demo"),
                },
            )

            print(f"🚀 Spawning {len(topics)} workers...")
            print(f"⚡ Concurrency: {concurrency} sandboxes in parallel")
            print()
            tasks = [
                research_topic(t, sandbox_client, sandbox_options, model, concurrency_sem, run_id, timeout_per_worker)
                for t in topics
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=False)
            
            # Write individual findings
            for r in results:
                finding_file = findings_dir / f"{r.topic.key}.md"
                with finding_file.open("w", encoding="utf-8") as f:
                    f.write(f"# {r.topic.name}\n\n")
                    f.write(f"**Source:** {r.topic.source}\n\n")
                    if r.success:
                        f.write(f"{r.answer}\n\n")
                    else:
                        f.write(f"**ERROR:** {r.error}\n\n")
                    f.write(f"*Elapsed: {r.elapsed:.1f}s*\n")

            # Aggregate into final answer
            final_md = output_dir / "final-answer.md"
            successful = [r for r in results if r.success]
            failed = [r for r in results if not r.success]
            
            with final_md.open("w", encoding="utf-8") as f:
                f.write("# Research Findings\n\n")
                f.write("Aggregated research across multiple topics.\n\n")
                f.write(f"**Analyzed:** {len(successful)}/{len(topics)} topics\n\n")
                
                for r in successful:
                    f.write(f"## {r.topic.name}\n\n")
                    f.write(f"{r.answer}\n\n")
                    f.write("---\n\n")
                
                if failed:
                    f.write("## Failed Topics\n\n")
                    for r in failed:
                        f.write(f"- **{r.topic.name}**: {r.error}\n")

            # Write JSON summary
            summary = {
                "run_id": run_id,
                "total_topics": len(topics),
                "successful": len(successful),
                "failed": len(failed),
                "elapsed_total": time.perf_counter() - t_start,
                "topics": [
                    {
                        "key": r.topic.key,
                        "name": r.topic.name,
                        "success": r.success,
                        "elapsed": r.elapsed,
                        "error": r.error if not r.success else None,
                    }
                    for r in results
                ],
            }
            with (output_dir / "summary.json").open("w") as f:
                json.dump(summary, f, indent=2)

            # Summary
            elapsed_total = time.perf_counter() - t_start
            print()
            print("=" * 72)
            print("🎉 SWARM COMPLETE")
            print("=" * 72)
            print(f"✅ Successful     : {len(successful)} / {len(topics)}")
            if failed:
                print(f"❌ Failed         : {len(failed)} / {len(topics)}")
            print(f"⏱️  Wall-clock time: {elapsed_total:.1f}s")
            print(f"📄 Output         : {final_md}")
            print()
            print("┌─────────────────────────────────────────────────────────────────────┐")
            print("│                    RESEARCH COMPLETE                                │")
            print("├─────────────────────────────────────────────────────────────────────┤")
            for r in successful:
                print(f"│  ✅ {r.topic.name:50s} {r.elapsed:5.1f}s │")
            for r in failed:
                print(f"│  ❌ {r.topic.name:50s} FAILED  │")
            print("└─────────────────────────────────────────────────────────────────────┘")
            print()

            return 0
    finally:
        await cred.close()
        await aoai_client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Swarm Research Agent — Parallel Multi-Agent Research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # Research 2 frameworks
  python swarm_research_agent.py \\
    --topic "fastapi|FastAPI|https://github.com/fastapi/fastapi|What makes FastAPI performant?" \\
    --topic "django|Django|https://github.com/django/django|What are Django's key features?"

  # Compare 3 container runtimes
  python swarm_research_agent.py \\
    --topic "kubernetes|Kubernetes|https://github.com/kubernetes/kubernetes|How does the scheduler work?" \\
    --topic "docker|Docker|https://github.com/docker/cli|What are Docker's isolation mechanisms?" \\
    --topic "containerd|containerd|https://github.com/containerd/containerd|What are containerd's responsibilities?" \\
    --concurrency 2

Topic format: key|name|source_url|question
  - key: lowercase-with-dashes (used for file names)
  - name: Display name for reports
  - source_url: GitHub repo or doc URL
  - question: Research question for the agent
        """,
    )
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="Add a research topic (format: key|name|source|question). Repeat for multiple topics.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent workers (default: 3)",
    )
    parser.add_argument(
        "--timeout-per-worker",
        type=int,
        default=180,
        help="Timeout per worker in seconds (default: 180)",
    )
    args = parser.parse_args(argv)

    if not args.topics:
        print("Error: At least one --topic is required.", file=sys.stderr)
        print("", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    try:
        topics = [parse_topic_arg(t) for t in args.topics]
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:8]
    output_dir = Path.cwd() / ".run-output" / f"swarm-{run_id}"

    return asyncio.run(run_swarm(topics, args.concurrency, args.timeout_per_worker, run_id, output_dir))


if __name__ == "__main__":
    sys.exit(main())
