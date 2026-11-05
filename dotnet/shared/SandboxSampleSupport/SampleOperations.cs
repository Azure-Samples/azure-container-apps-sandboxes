using System.Diagnostics;
using Azure;
using Microsoft.Azure.ContainerApps.Sandbox;
using Microsoft.Azure.ContainerApps.Sandbox.Models;

namespace SandboxSampleSupport;

public static class SampleOperations
{
    public static CreateSandboxContent FromPublicDisk(string disk, string scenario)
    {
        var content = new CreateSandboxContent
        {
            SourcesRef = new SandboxSource
            {
                DiskImage = new SandboxSourceDiskImage { Name = disk, IsPublic = true }
            },
            Resources = new SandboxResources("1000m", "2048Mi")
        };
        content.Labels["scenario"] = scenario;
        content.Labels["language"] = "dotnet";
        content.Labels["run-id"] = Guid.NewGuid().ToString("N");
        return content;
    }

    public static async Task WaitForRunningAsync(SandboxResource sandbox, CancellationToken cancellationToken)
    {
        var timer = Stopwatch.StartNew();
        while (timer.Elapsed < TimeSpan.FromMinutes(3))
        {
            var current = (await sandbox.GetAsync(cancellationToken)).Value;
            if (current.Data?.State == SandboxState.Running)
            {
                return;
            }

            if (current.Data?.State is { } state &&
                state != SandboxState.Creating && state != SandboxState.Idle)
            {
                throw new InvalidOperationException($"Sandbox {sandbox.Id} entered state {state}.");
            }

            await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken);
        }

        throw new TimeoutException($"Sandbox {sandbox.Id} did not become Running.");
    }

    public static async Task<SandboxExecuteShellCommandResult> RunShellAsync(
        SandboxResource sandbox, string command, CancellationToken cancellationToken)
    {
        var result = (await sandbox.ExecuteShellCommandAsync(
            new ExecuteSandboxShellCommandContent(command), cancellationToken: cancellationToken)).Value;
        if (result.ExitCode != 0)
        {
            throw new InvalidOperationException(
                $"Command failed ({result.ExitCode}): {result.StandardError}\n{result.StandardOutput}");
        }

        return result;
    }

    public static async Task UploadAsync(
        SandboxResource sandbox, string localPath, string remotePath, CancellationToken cancellationToken)
    {
        using var stream = File.OpenRead(localPath);
        var result = (await sandbox.WriteSandboxFileAsync(
            remotePath, stream, createDirs: true, cancellationToken: cancellationToken)).Value;
        if (!result.Success)
        {
            throw new InvalidOperationException($"Upload to {remotePath} failed: {result.Error}");
        }
    }

    public static async Task CleanupAsync(string description, Func<CancellationToken, Task> delete)
    {
        // Cleanup must still run after Ctrl+C or an operation timeout.
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(2));
        try
        {
            await delete(timeout.Token);
            Console.WriteLine($"Deleted {description}.");
        }
        catch (RequestFailedException exception) when (exception.Status == 404)
        {
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Cleanup failed for {description}: {exception.Message}. Delete it manually.");
            Environment.ExitCode = 1;
        }
    }

    public static async Task<int> RunAsync(Func<CancellationToken, Task<int>> action)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(20));
        ConsoleCancelEventHandler handler = (_, args) =>
        {
            args.Cancel = true;
            timeout.Cancel();
        };
        Console.CancelKeyPress += handler;
        try
        {
            var exitCode = await action(timeout.Token);
            return Environment.ExitCode == 0 ? exitCode : Environment.ExitCode;
        }
        catch (OperationCanceledException) when (timeout.IsCancellationRequested)
        {
            Console.Error.WriteLine("Cancelled or exceeded the 20-minute sample timeout.");
            return 1;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception.Message);
            return 1;
        }
        finally
        {
            Console.CancelKeyPress -= handler;
        }
    }
}
