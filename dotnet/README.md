# .NET samples for Azure Container Apps Sandboxes

These samples call the **.NET SDK directly** through the
`Microsoft.Azure.ContainerApps.Sandbox` NuGet package (version `1.0.0-beta.1`).
They do not use Python or wrap the CLI for data-plane
operations. The five .NET samples are numbered sequentially from 01 to 05;
their READMEs identify the corresponding Python scenarios.

## Prerequisites

- .NET 10 SDK.
- Azure CLI, authenticated with `az login`.
- An Azure subscription with permission to create resources and role assignments.
- Access to the SDK NuGet package, either from a feed or a local package directory.
- PowerShell 7 for the one-time setup script.

## Restore the SDK package

After the preview package is published to your configured feed, restore normally:

```powershell
dotnet restore dotnet\Sandboxes.Samples.slnx
```

Before publication, place `Microsoft.Azure.ContainerApps.Sandbox.1.0.0-beta.1.nupkg`
in a local directory and add it as a source for this restore only:

```powershell
dotnet restore dotnet\Sandboxes.Samples.slnx -p:RestoreAdditionalProjectSources=C:\path\to\packages
```

No SDK source checkout is required. Ensure your configured feeds can resolve the
package's dependencies. For a local prerelease, use `--no-restore` with subsequent
builds, and `--no-build --no-restore` with runs after building.
The SDK uses Azure.Core 1.63.0, which already includes the
`Azure.Identity` credential types. Do not add the older standalone Azure.Identity
package alongside it, because that causes duplicate-type compilation errors.

## Set up Azure resources

Run these commands from the repository root:

```powershell
az login
pwsh -File dotnet\setup\setup.ps1
```

This creates `aca-sandboxes-dotnet-samples-rg` and the sandbox group
`aca-sandboxes-dotnet-samples` in `eastus2`, grants the signed-in user
**Container Apps SandboxGroup Data Owner** at group scope, and writes
`dotnet\samples\.env`. It does not create workload sandboxes.
Allow 30-60 seconds for role propagation before running a sample.

Override the defaults if needed:

```powershell
pwsh -File dotnet\setup\setup.ps1 -SubscriptionId <subscription-id> -ResourceGroup <resource-group> -SandboxGroup <sandbox-group> -Region eastus2
```

For automation, supply `-PrincipalId <object-id> -PrincipalType ServicePrincipal`.
The script uses Azure CLI authentication and does not switch the active
subscription globally.

Existing resources can be used without running setup. Set these variables in
your environment or in `dotnet\samples\.env`:

```dotenv
AZURE_SUBSCRIPTION_ID=<subscription-id>
ACA_RESOURCE_GROUP=<resource-group>
ACA_SANDBOX_GROUP=<sandbox-group>
ACA_SANDBOXGROUP_REGION=eastus2
```

Environment variables take precedence over the file. The file is gitignored.
The .NET samples do not read or overwrite `python\samples\.env`.

## Run samples

| Scenario | Command from the repository root | What it shows |
|---|---|---|
| [01: Web app](samples/01-webapps/simple-anonymous) | `dotnet run --project dotnet\samples\01-webapps\simple-anonymous` | Upload, exec, readiness, anonymous HTTPS port, response checks, cleanup |
| [02: Coding agent](samples/02-coding-agents/gh-copilot-cli) | `dotnet run --project dotnet\samples\02-coding-agents\gh-copilot-cli` | Copilot CLI install, deny-default egress, portal PAT setup, cleanup |
| [03: Data pipeline](samples/03-data-processing) | `dotnet run --project dotnet\samples\03-data-processing` | Three C# workers, durable shared AzureBlob volume, verified aggregate report, cleanup |
| [04: Ephemeral CI](samples/04-developer-workflows) | `dotnet run --project dotnet\samples\04-developer-workflows` | Prepare a .NET runner, snapshot, parallel isolated PR checks, cleanup |
| [05: Excalidraw MCP](samples/05-mcp-hosting/excalidraw-anonymous) | `dotnet run --project dotnet\samples\05-mcp-hosting\excalidraw-anonymous` | Build upstream MCP server, anonymous HTTPS, initialize and tools/list checks, cleanup |

For scenario 03, publish the self-contained Linux worker before building/running
the host; see its README. Scenarios 02 and 05 wait for Enter before cleanup;
pass `-- --validate` for unattended validation and automatic cleanup. Scenario 02
validates installation and policy, not authenticated Copilot chat: PAT setup is
manual by design, matching the Python portal-paste flow.
Only the variants listed here are implemented; this is not full Python sample parity.

The samples use `SandboxGroupClient.CreateSandboxAsync`,
`SandboxResource.WriteSandboxFileAsync`, and the SDK's typed command, port, and
snapshot APIs. Unlike the Python SDK's `begin_*().result()`, this .NET SDK returns
resource responses directly; the samples explicitly poll sandbox readiness.
`GetAsync()` returns a new resource, not an update to the original `Data`.

Each run has a 20-minute timeout and handles Ctrl+C. Cleanup uses an independent
timeout so cancellation does not skip deletion. If deletion fails, the resource
ID is printed and the sample exits unsuccessfully. Delete that resource manually.

## Offline checks

These commands do not authenticate to Azure or create resources:

```powershell
dotnet build dotnet\Sandboxes.Samples.slnx
dotnet test dotnet\Sandboxes.Samples.slnx --no-build
dotnet format dotnet\Sandboxes.Samples.slnx --verify-no-changes --no-restore
pwsh -File dotnet\tests\Test-Setup.ps1
```

The unit tests cover configuration precedence, fresh-resource readiness checks,
cancellation, remote command failures, cleanup, the concurrent data pipeline,
and JSON/SSE MCP responses. The PowerShell checks mock
Azure CLI to verify setup configuration, repeatability, and fail-fast behavior
without touching Azure. The CI sample README also
documents how to run its C# workloads locally.

The existing Python GitHub Actions workflows are unchanged.

## Cleanup

Samples delete the sandboxes and snapshots they create, but setup leaves its
resource group and sandbox group in place. Azure usage can incur charges.
If the default resource group was created only for these samples, remove it
when finished:

```powershell
az group delete --name aca-sandboxes-dotnet-samples-rg --subscription <subscription-id> --yes
```

Deleting the resource group removes everything in it. Do not use this command
against a resource group containing unrelated workloads.
