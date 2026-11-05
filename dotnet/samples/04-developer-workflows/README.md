# 04: Snapshot-backed ephemeral CI (.NET)

This follows the Python [06-developer-workflows](../../../python/samples/06-developer-workflows)
CI scenario, but both the host and calculator workloads
are C#. No Python runtime or `uv` is required.

Complete the [one-time setup](../../README.md), then run from the repo root:

```powershell
dotnet run --project dotnet\samples\04-developer-workflows
dotnet run --project dotnet\samples\04-developer-workflows -- pr-1 pr-2
```

## How it works

1. Create a sandbox from the public `ubuntu` disk.
2. Install the .NET 10 SDK into `/opt/dotnet` using Microsoft's
   `dotnet-install.sh`, upload the baseline C# project, and restore/build it.
3. Capture a snapshot with `CreateSnapshotAsync`.
4. Restore one sandbox per selected PR from `SandboxSourceSnapshot`, upload
   its `Calculator.cs`, rebuild without restore, and execute its checks.
5. Wait for all parallel jobs and delete the worker sandboxes, snapshot, and
   baseline sandbox, including on failure or cancellation.

The initial SDK download can take several minutes and requires outbound HTTPS
access to Microsoft's .NET download services. `ACA_CI_DISK` can select another
public base disk with bash, curl, and the Linux .NET runtime prerequisites.
A disk that already contains `/opt/dotnet/dotnet` skips installation.
Invariant globalization is enabled for this numeric-only demo.

The small test executable has no external package dependencies. It checks
addition, subtraction, multiplication, and multiplication by zero. This keeps
the snapshot example focused on isolation rather than a test-framework feed.
For production, replace it with your repository's `dotnet test` workflow.

| PR | Change | Expected result |
|---|---|---|
| `pr-1` | Baseline calculator | PASS |
| `pr-2` | Checked arithmetic | PASS |
| `pr-3` | Intentional regression: multiplication adds instead | FAIL |

The default run intentionally exits with **2**, because `pr-3` fails.
Exit **0** means all selected checks passed; **1** means infrastructure,
configuration, build, cancellation, or cleanup failure.

## Check workloads locally without Azure

Run each command from the repository root. The expected exit codes are 0, 0,
and 2 respectively:

```powershell
dotnet run --project dotnet\samples\04-developer-workflows\workload\CalculatorChecks.csproj -p:CalculatorSource=pr-1\Calculator.cs
dotnet run --project dotnet\samples\04-developer-workflows\workload\CalculatorChecks.csproj -p:CalculatorSource=pr-2\Calculator.cs
dotnet run --project dotnet\samples\04-developer-workflows\workload\CalculatorChecks.csproj -p:CalculatorSource=pr-3\Calculator.cs
```
