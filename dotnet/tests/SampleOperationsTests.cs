using Azure;
using Microsoft.Azure.ContainerApps.Sandbox;
using Microsoft.Azure.ContainerApps.Sandbox.Models;
using NUnit.Framework;
using SandboxSampleSupport;

namespace SandboxSamples.Tests;

public class SampleOperationsTests
{
    [Test]
    public void EnvironmentFilePreservesOverridesAndParsesQuotedValues()
    {
        var first = $"ACA_TEST_{Guid.NewGuid():N}";
        var second = $"ACA_TEST_{Guid.NewGuid():N}";
        var file = Path.GetTempFileName();
        try
        {
            Environment.SetEnvironmentVariable(first, "override");
            File.WriteAllText(file, $"# comment\n{first}=file\n{second}=\"value=with=equals\"\ninvalid\n");
            SampleEnvironment.LoadFile(file);
            Assert.That(Environment.GetEnvironmentVariable(first), Is.EqualTo("override"));
            Assert.That(Environment.GetEnvironmentVariable(second), Is.EqualTo("value=with=equals"));
        }
        finally
        {
            Environment.SetEnvironmentVariable(first, null);
            Environment.SetEnvironmentVariable(second, null);
            File.Delete(file);
        }
    }

    [Test]
    public async Task ReadinessUsesFreshResourceRatherThanCachedData()
    {
        var sandbox = new TestSandbox(SandboxState.Running);
        await SampleOperations.WaitForRunningAsync(sandbox, CancellationToken.None);
        Assert.That(sandbox.GetCalls, Is.EqualTo(1));
        Assert.That(sandbox.Data.State, Is.EqualTo(SandboxState.Creating));
    }

    [Test]
    public void ReadinessRejectsStoppedSandbox()
    {
        var sandbox = new TestSandbox(SandboxState.Stopped);
        Assert.ThrowsAsync<InvalidOperationException>(() =>
            SampleOperations.WaitForRunningAsync(sandbox, CancellationToken.None));
    }

    [Test]
    public void ReadinessHonorsCancellation()
    {
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        Assert.ThrowsAsync<OperationCanceledException>(() =>
            SampleOperations.WaitForRunningAsync(new TestSandbox(SandboxState.Creating), cancelled.Token));
    }

    [Test]
    public void ShellFailureIncludesRemoteOutput()
    {
        var sandbox = new TestSandbox(SandboxState.Running, exitCode: 7);
        var exception = Assert.ThrowsAsync<InvalidOperationException>(() =>
            SampleOperations.RunShellAsync(sandbox, "exit 7", CancellationToken.None));
        Assert.That(exception!.Message, Does.Contain("7").And.Contain("remote error").And.Contain("remote output"));
    }

    [Test]
    public async Task CleanupStillRunsWhenTheSampleWasCancelled()
    {
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        var deleted = false;
        var result = await SampleOperations.RunAsync(async _ =>
        {
            try
            {
                cancelled.Token.ThrowIfCancellationRequested();
                return 0;
            }
            finally
            {
                await SampleOperations.CleanupAsync("test resource", token =>
                {
                    Assert.That(token.IsCancellationRequested, Is.False);
                    deleted = true;
                    return Task.CompletedTask;
                });
            }
        });
        Assert.That(result, Is.EqualTo(1));
        Assert.That(deleted, Is.True);
    }

    private sealed class TestSandbox(SandboxState refreshedState, int exitCode = 0) : SandboxResource
    {
        public override string Id => "test-sandbox";
        public override SandboxProperties Data { get; } =
            MicrosoftAzureContainerAppsSandboxModelFactory.SandboxProperties(state: SandboxState.Creating);
        public int GetCalls { get; private set; }

        public override Task<Response<SandboxResource>> GetAsync(CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            GetCalls++;
            return Task.FromResult(Response.FromValue<SandboxResource>(
                new RefreshedSandbox(refreshedState), null!));
        }

        public override Task<Response<SandboxExecuteShellCommandResult>> ExecuteShellCommandAsync(
            ExecuteSandboxShellCommandContent body, string containerName = default!,
            CancellationToken cancellationToken = default) =>
            Task.FromResult(Response.FromValue(
                MicrosoftAzureContainerAppsSandboxModelFactory.SandboxExecuteShellCommandResult(
                    exitCode, "remote output", "remote error"), null!));
    }

    private sealed class RefreshedSandbox(SandboxState state) : SandboxResource
    {
        public override SandboxProperties Data { get; } =
            MicrosoftAzureContainerAppsSandboxModelFactory.SandboxProperties(state: state);
    }
}
