# Sandbox scenarios

Composed use cases that combine multiple sandbox capabilities. Most
scenarios here are runnable today; `03`, `05`, and `06` are
placeholders — start with the [guides](../guides/) for runnable code
on those topics.

| # | Scenario | What it shows | Status |
|---|---|---|---|
| 01 | [webapps](01-webapps) | Run a web app in a sandbox; patterns include `simple-anonymous` (open to the internet) and (planned) `authenticated` (Entra-gated) | ✅ ready |
| 02 | [coding-agents](02-coding-agents) | Run **Copilot CLI** in a sandbox with deny-default egress + portal-paste PAT injection (Python + CLI). Claude Code / Codex stubs included. | ✅ Copilot CLI ready |
| 03 | [code-interpreter](03-code-interpreter) | LLM-driven code execution — generate, run, observe, iterate | 📝 planned |
| 04 | [swarms](04-swarms) | Many sandboxes, one orchestrator — fan-out work across N workers (`sandbox-inception`, `shared-blob-memory`) | ✅ ready |
| 05 | [data-processing](05-data-processing) | Producer/consumer pipelines on shared AzureBlob volumes | 📝 planned |
| 06 | [developer-workflows](06-developer-workflows) | PR builds, ephemeral CI, on-demand dev environments | 📝 planned |
| 07 | [computer-use](07-computer-use) | LLM computer-use agent (Azure OpenAI `computer-use-preview`) driving Chromium inside a sandbox to fill out a form; watch live via noVNC | ✅ OpenAI ready |
| 08 | [sandbox-agents](08-sandbox-agents) | Wire agent frameworks (OpenAI Agents SDK, LangChain, Anthropic) to a sandbox as their tool-execution environment | ✅ ready |
| 09 | [mcp-hosting](09-mcp-hosting) | Host **Model Context Protocol (MCP)** servers in sandboxes (`excalidraw-anonymous`, `dab-sql-devtunnel`) for AI clients to connect over HTTPS | ✅ ready |
| 10 | [connectors-email-triage](10-connectors-email-triage) | **Connector Namespaces + Sandbox**: Outlook `When a new email arrives` trigger → ACA receiver → per-email sandbox → GitHub Copilot CLI → Teams MCP (Work IQ) posts a triage card. End-to-end `azd up`. | ✅ ready |
