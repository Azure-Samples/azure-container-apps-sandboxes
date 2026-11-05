# 05: Excalidraw MCP hosting (.NET)

Ports the Python [09-mcp-hosting/excalidraw-anonymous](../../../../python/samples/09-mcp-hosting/excalidraw-anonymous)
scenario using the Sandbox .NET SDK.
The host is C#; the upstream MCP server runs Node.js inside the sandbox.

Complete the [SDK and resource setup](../../../README.md), then run:

```powershell
dotnet run --project dotnet\samples\05-mcp-hosting\excalidraw-anonymous
```

The sample creates a sandbox from the public `copilot` disk (override with
`ACA_MCP_DISK`), clones and builds the upstream Excalidraw MCP server, verifies
local initialization, exposes port 80 anonymously, then performs real public
HTTPS `initialize`, `notifications/initialized`, and `tools/list` requests.
It handles JSON and SSE-framed JSON-RPC responses and session/protocol headers.
The upstream commit used for the build is printed for reproducibility.

**The URL is public and anonymous.** Only use demo data. Press Enter to delete
the sandbox and its routing. Failure, cancellation, and the twenty-minute
timeout also trigger cleanup. TLS certificate verification remains enabled
during dependency installation.

For unattended live validation with automatic cleanup:

```powershell
dotnet run --project dotnet\samples\05-mcp-hosting\excalidraw-anonymous -- --validate
```

Exit 0 means the protocol checks and cleanup passed; exit 1 means failure.
This sample does not cover the separate Python DAB SQL + Dev Tunnels variant,
which requires interactive Dev Tunnels authentication.
