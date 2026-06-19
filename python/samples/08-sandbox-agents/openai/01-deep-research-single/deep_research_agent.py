"""Deep Research Agent — analyze any GitHub repository.

This demo shows how to build a research agent that:
1. Clones any public GitHub repository inside an isolated sandbox
2. Analyzes the code, docs, and structure
3. Answers questions about the repository

Ready-to-run examples::

    # Example 1: Analyze OpenAI Agents SDK architecture
    python deep_research_agent.py \\
      --repo https://github.com/openai/openai-agents-python \\
      "What are the main agent capabilities and how do they work together?"

    # Example 2: Understand Kubernetes scheduler
    python deep_research_agent.py \\
      --repo https://github.com/kubernetes/kubernetes \\
      "How does the Kubernetes scheduler decide which node to place a pod on?"

    # Example 3: Explore FastAPI framework
    python deep_research_agent.py \\
      --repo https://github.com/tiangolo/fastapi \\
      "What makes FastAPI fast? Explain the async architecture."

    # Example 4: Research LangChain structure
    python deep_research_agent.py \\
      --repo https://github.com/langchain-ai/langchain \\
      "What are the core abstractions in LangChain?"

    # Example 5: Analyze this repo (ACA Sandboxes samples)
    python deep_research_agent.py \\
      "How does ACA Sandboxes implement egress control and security?"

The agent gets Shell + Filesystem capabilities and full access to the cloned
repo. It can grep, read files, run basic commands, and synthesize findings.

Configuration: see ``.env.example`` for Azure OpenAI and ACA sandbox group settings.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# Make ``demo_support`` importable when invoked directly from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.model_provider import build_azure_openai_model  # noqa: E402

DEFAULT_REPO = "https://github.com/Azure/azure-functions-host"
DEFAULT_QUESTION = "What is this repository about? Summarize its main purpose and structure."

INSTRUCTIONS_TEMPLATE = """\
You are a research agent running inside an isolated Azure Container Apps sandbox.
Your task is to analyze a GitHub repository and answer questions about it.

Repository location: {repo_path}

Operating procedure:
1. First, clone the repository:
   cd /workspace && git clone --depth 1 {repo_url} repo
   
2. Explore the repository structure:
   - Read README.md or README files at the root
   - List directories to understand organization: ls -la /workspace/repo/
   - Look for key files: package.json, requirements.txt, go.mod, etc.
   
3. To answer the question:
   - Use grep to search: grep -RIn "keyword" /workspace/repo/
   - Read relevant files with cat or the Filesystem tool
   - Analyze code structure, documentation, and comments
   
4. Cite your sources:
   - Reference specific files you read
   - Include line numbers for code snippets if relevant
   
5. Be honest: if the information isn't in the repository or you can't find it,
   say so plainly rather than guess.

Output format:
- Direct answer to the question
- Key findings with file citations
- Optional: relevant code snippets or examples
"""

async def run_demo(repo_url: str, question: str) -> int:
    # Local imports keep startup snappy.
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.identity.aio import DefaultAzureCredential

    from agents import Runner, set_tracing_disabled
    from agents.run_config import RunConfig
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.sandbox.capabilities import Filesystem, Shell

    from agents_aca_sandboxes import (
        ACASandboxesClient,
        ACASandboxesClientOptions,
        ACASandboxVolumeMount,
        load_config,
    )

    # Disable OpenAI tracing (we use Azure OpenAI, not OpenAI platform)
    set_tracing_disabled(True)

    cfg = load_config()
    model, aoai_client = build_azure_openai_model()
    run_id = uuid.uuid4().hex[:8]
    
    # Extract repo name from URL for display
    repo_name = repo_url.rstrip('/').split('/')[-1]
    if repo_name.endswith('.git'):
        repo_name = repo_name[:-4]

    print("=" * 72)
    print("DEEP RESEARCH AGENT — Repository Analysis")
    print("=" * 72)
    print(f"==> ACA sandbox group : {cfg.sandbox_group} ({cfg.region})")
    print(f"==> AOAI deployment   : {os.environ.get('AZURE_OPENAI_DEPLOYMENT')}")
    print(f"==> Demo run id       : {run_id}")
    print(f"==> Repository        : {repo_url}")
    print(f"==> Repo name         : {repo_name}")
    print(f"==> Question          : {question}")
    print()
    
    # Show architecture diagram
    print("┌─────────────────────────────────────────────────────────────────────┐")
    print("│              SINGLE-SANDBOX ARCHITECTURE                            │")
    print("├─────────────────────────────────────────────────────────────────────┤")
    print("│                                                                     │")
    print("│   Runner (local)                                                    │")
    print("│      │                                                               │")
    print("│      └──> Question                                                  │")
    print("│             │                                                        │")
    print("│             v                                                        │")
    print("│      ┌─────────────────────────────────────────────┐               │")
    print("│      │      ACA Sandbox (isolated microVM)         │               │")
    print("│      │                                              │               │")
    print("│      │  1. 📦 Provision Ubuntu environment          │               │")
    print("│      │  2. 🔄 Git clone repository                  │               │")
    print("│      │  3. 🔍 Agent explores (grep, cat, ls)        │               │")
    print("│      │  4. 🧠 Agent analyzes code & docs            │               │")
    print("│      │  5. 📝 Agent synthesizes answer              │               │")
    print("│      │  6. ✅ Return findings with citations        │               │")
    print("│      │  7. 🧹 Auto-cleanup sandbox                  │               │")
    print("│      │                                              │               │")
    print("│      └─────────────────────────────────────────────┘               │")
    print("│                                                                     │")
    print("│   Sandbox features:                                                 │")
    print("│   • Fully isolated Ubuntu microVM                                  │")
    print("│   • Shell & Filesystem capabilities                                │")
    print("│   • Git pre-installed for cloning                                  │")
    print("│   • No access to host system                                       │")
    print("│                                                                     │")
    print("└─────────────────────────────────────────────────────────────────────┘")
    print()

    cred = DefaultAzureCredential()
    try:
        async with SandboxGroupClient(
            endpoint=cfg.endpoint,
            credential=cred,
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            sandbox_group=cfg.sandbox_group,
        ) as gc:
            sandbox_client = ACASandboxesClient(gc)
            options = ACASandboxesClientOptions(
                disk="ubuntu",
                labels={
                    "demo": "deep-research",
                    "run-id": run_id,
                    "owner": os.environ.get("USERNAME", "demo"),
                },
            )

            instructions = INSTRUCTIONS_TEMPLATE.format(
                repo_url=repo_url,
                repo_path="/workspace/repo"
            )
            
            agent = SandboxAgent(
                name="repo-researcher",
                instructions=instructions,
                capabilities=[Shell(), Filesystem()],
                model=model,
            )
            run_config = RunConfig(
                sandbox=SandboxRunConfig(
                    client=sandbox_client,
                    options=options,
                    manifest={},  # Empty — agent will git clone
                ),
            )

            print("🚀 Provisioning sandbox (no manifest)...")
            print("🤖 Agent will clone repo and research...")
            print()
            
            import time
            t_start = time.perf_counter()
            
            result = await Runner.run(
                agent,
                question,
                run_config=run_config,
                max_turns=15,  # Extra turns for git clone + analysis
            )
            
            elapsed = time.perf_counter() - t_start

            print()
            print("=" * 72)
            print("✅ RESEARCH COMPLETE")
            print("=" * 72)
            print(f"⏱️  Total time: {elapsed:.1f}s")
            print()
            print("┌─────────────────────────────────────────────────────────────────────┐")
            print("│                         ANSWER                                      │")
            print("└─────────────────────────────────────────────────────────────────────┘")
            print()
            print(result.final_output)
            print()

            return 0

    finally:
        await cred.close()
        await aoai_client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deep Research Agent — Analyze GitHub Repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Ready-to-run examples (copy-paste):

  # Analyze OpenAI Agents SDK
  python deep_research_agent.py \\
    --repo https://github.com/openai/openai-agents-python \\
    "What are the main agent capabilities?"
  
  # Understand Kubernetes scheduler
  python deep_research_agent.py \\
    --repo https://github.com/kubernetes/kubernetes \\
    "How does the scheduler decide pod placement?"
  
  # Explore FastAPI performance
  python deep_research_agent.py \\
    --repo https://github.com/tiangolo/fastapi \\
    "What makes FastAPI fast?"

See EXAMPLES.md for more ready-to-run commands!
""",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repository URL to analyze (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=DEFAULT_QUESTION,
        help="Question to answer about the repository",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run_demo(args.repo, args.question))


if __name__ == "__main__":
    raise SystemExit(main())
