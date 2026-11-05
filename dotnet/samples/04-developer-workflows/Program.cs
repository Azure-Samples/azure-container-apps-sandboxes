using Microsoft.Azure.ContainerApps.Sandbox;
using Microsoft.Azure.ContainerApps.Sandbox.Models;
using SandboxSampleSupport;

return await SampleOperations.RunAsync(async cancellationToken =>
{
    string[] selected = args.Length == 0 ? ["pr-1", "pr-2", "pr-3"] : args.Distinct().ToArray();
    if (selected.Any(name => name is not ("pr-1" or "pr-2" or "pr-3")))
    {
        throw new ArgumentException("Choose pr-1, pr-2, or pr-3.");
    }

    var group = SampleEnvironment.CreateClient();
    var disk = Environment.GetEnvironmentVariable("ACA_CI_DISK") ?? "ubuntu";
    var baseline = (await group.CreateSandboxAsync(
        SampleOperations.FromPublicDisk(disk, "04-developer-workflows"), cancellationToken)).Value;
    SnapshotResource? snapshot = null;
    try
    {
        Console.WriteLine($"Preparing base sandbox {baseline.Id}...");
        await SampleOperations.WaitForRunningAsync(baseline, cancellationToken);
        await SampleOperations.RunShellAsync(baseline,
            "if [ ! -x /opt/dotnet/dotnet ]; then " +
            "curl -fsSL --max-time 60 https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh && " +
            "bash /tmp/dotnet-install.sh --channel 10.0 --install-dir /opt/dotnet --no-path; fi",
            cancellationToken);
        foreach (var file in new[] { "CalculatorChecks.csproj", "Program.cs" })
        {
            await SampleOperations.UploadAsync(baseline,
                Path.Combine(AppContext.BaseDirectory, "workload", file), $"/workspace/{file}", cancellationToken);
        }
        await SampleOperations.UploadAsync(baseline,
            Path.Combine(AppContext.BaseDirectory, "workload", "pr-1", "Calculator.cs"),
            "/workspace/Calculator.cs", cancellationToken);
        await SampleOperations.RunShellAsync(baseline,
            "cd /workspace && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 " +
            "DOTNET_CLI_TELEMETRY_OPTOUT=1 /opt/dotnet/dotnet build --ignore-failed-sources",
            cancellationToken);

        snapshot = (await group.CreateSnapshotAsync(
            baseline.Id, new CreateSnapshotContent(), cancellationToken)).Value;
        Console.WriteLine($"Warm snapshot: {snapshot.Id}");
        var snapshotId = snapshot.Id;
        var results = await Task.WhenAll(selected.Select(name => RunPrAsync(
            group, snapshotId, name, cancellationToken)));
        foreach (var result in results)
        {
            Console.WriteLine($"{result.Name}: {(result.Passed ? "PASS" : "FAIL")}");
        }
        return results.All(result => result.Passed) ? 0 : 2;
    }
    finally
    {
        if (snapshot is not null)
        {
            await SampleOperations.CleanupAsync($"snapshot {snapshot.Id}",
                token => snapshot.DeleteSnapshotAsync(cancellationToken: token));
        }
        await SampleOperations.CleanupAsync($"base sandbox {baseline.Id}",
            token => baseline.DeleteAsync(cancellationToken: token));
    }
});

static async Task<(string Name, bool Passed)> RunPrAsync(
    SandboxGroupClient group, string snapshotId, string name, CancellationToken cancellationToken)
{
    var content = new CreateSandboxContent
    {
        SourcesRef = new SandboxSource { Snapshot = new SandboxSourceSnapshot(snapshotId) }
    };
    content.Labels["scenario"] = "04-developer-workflows";
    content.Labels["language"] = "dotnet";
    content.Labels["pr"] = name;
    var sandbox = (await group.CreateSandboxAsync(content, cancellationToken)).Value;
    Console.WriteLine($"{name}: sandbox {sandbox.Id}");
    try
    {
        await SampleOperations.WaitForRunningAsync(sandbox, cancellationToken);
        await SampleOperations.UploadAsync(sandbox,
            Path.Combine(AppContext.BaseDirectory, "workload", name, "Calculator.cs"),
            "/workspace/Calculator.cs", cancellationToken);
        await SampleOperations.RunShellAsync(sandbox,
            "cd /workspace && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 " +
            "DOTNET_CLI_TELEMETRY_OPTOUT=1 /opt/dotnet/dotnet build --no-restore",
            cancellationToken);
        var result = (await sandbox.ExecuteShellCommandAsync(
            new ExecuteSandboxShellCommandContent(
                "cd /workspace && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 " +
                "/opt/dotnet/dotnet bin/Debug/net10.0/CalculatorChecks.dll"),
            cancellationToken: cancellationToken)).Value;
        Console.WriteLine($"{name}: {result.StandardOutput}{result.StandardError}");
        if (result.ExitCode is not (0 or 2))
        {
            throw new InvalidOperationException($"{name}: test runner failed with exit code {result.ExitCode}.");
        }
        return (name, result.ExitCode == 0);
    }
    finally
    {
        await SampleOperations.CleanupAsync($"sandbox {sandbox.Id}",
            token => sandbox.DeleteAsync(cancellationToken: token));
    }
}
