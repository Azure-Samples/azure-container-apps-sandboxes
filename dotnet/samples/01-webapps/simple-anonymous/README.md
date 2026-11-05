# 01: Anonymous web app (.NET)

The C# host uses the preview Sandbox .NET SDK NuGet package. The sandbox runs the same
dependency-free Node app as the Python example. The project links the existing
`server.js` into its output; no Python runtime, `uv`, or `npm install` is needed.

Complete the [one-time setup](../../../README.md), then run from the repo root:

```powershell
dotnet run --project dotnet\samples\01-webapps\simple-anonymous
```

The sample creates a sandbox from the public `node-22` disk, waits for Running,
uploads `/app/server.js`, starts Node, and polls `/healthz` inside the sandbox.
It calls `AddPortAsync` with `Anonymous = true`, checks `/healthz`,
`/api/hello`, `/api/info`, and the HTML landing page from the host, then deletes
the sandbox. Deletion also removes its port routing.

**The URL is public and anonymous.** Serve only demo data. The URL is temporary
and stops working when the sample finishes; this is a smoke-check example,
not a persistent deployment.

Override `ACA_WEBAPP_DISK` to use another public disk with Node and curl.
The host exits with code 0 on success or 1 on failure.
