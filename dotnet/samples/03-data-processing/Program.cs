using System.Text.Json;
using Microsoft.Azure.ContainerApps.Sandbox;
using Microsoft.Azure.ContainerApps.Sandbox.Models;
using SandboxSampleSupport;

return await SampleOperations.RunAsync(async cancellationToken =>
{
    var worker = Path.Combine(AppContext.BaseDirectory, "workload", "PipelineWorker");
    if (!File.Exists(worker))
    {
        throw new FileNotFoundException(
            "Publish the Linux worker first, then rebuild the host. See the 03-data-processing README.", worker);
    }
    SampleEnvironment.Load();
    var batches = ReadInt("PIPELINE_BATCHES", 20, 1, 1000);
    var events = ReadInt("PIPELINE_EVENTS_PER_BATCH", 100, 1, 10000);
    var delay = ReadInt("PIPELINE_BATCH_DELAY_MS", 500, 0, 10000);
    var seed = ReadInt("PIPELINE_SEED", 42, 0, int.MaxValue);
    var group = SampleEnvironment.CreateClient();
    var volumeName = $"pipeline-dotnet-{Guid.NewGuid():N}";
    VolumeResource? volume = null;
    try
    {
        volume = (await group.CreateVolumeAsync(
            volumeName, new ServiceManagedBlobVolume(), cancellationToken)).Value;
        Console.WriteLine($"Volume: {volumeName}");
        await Task.WhenAll(RunWorkerAsync("producer"), RunWorkerAsync("transformer"));
        // The producer and transformer are deleted before a new sandbox reads their durable output.
        var report = await RunWorkerAsync("aggregator");
        var line = report.Split('\n').Single(item => item.StartsWith("RESULT=", StringComparison.Ordinal));
        using var json = JsonDocument.Parse(line["RESULT=".Length..]);
        if (json.RootElement.GetProperty("events_total").GetInt32() != checked(batches * events) ||
            json.RootElement.GetProperty("files_read").GetInt32() != batches)
        {
            throw new InvalidOperationException($"Unexpected aggregate report: {line}");
        }
        Console.WriteLine(line);
        Console.WriteLine("Pipeline checks passed: all events processed once and read from a separate sandbox.");
        return 0;
    }
    finally
    {
        if (volume is not null)
        {
            await SampleOperations.CleanupAsync($"volume {volumeName}",
                token => volume.DeleteVolumeAsync(token));
        }
    }

    async Task<string> RunWorkerAsync(string role)
    {
        var content = SampleOperations.FromPublicDisk("ubuntu", "03-data-processing");
        content.Labels["role"] = role;
        content.Volumes.Add(new SandboxVolume(volumeName, "/mnt/shared"));
        var sandbox = (await group.CreateSandboxAsync(content, cancellationToken)).Value;
        Console.WriteLine($"{role}: sandbox {sandbox.Id}");
        try
        {
            await SampleOperations.WaitForRunningAsync(sandbox, cancellationToken);
            await SampleOperations.UploadAsync(sandbox, worker, "/tmp/PipelineWorker", cancellationToken);
            var result = await SampleOperations.RunShellAsync(sandbox,
                $"chmod +x /tmp/PipelineWorker && /tmp/PipelineWorker {role} /mnt/shared {batches} {events} {delay} {seed} 180",
                cancellationToken);
            if (role == "aggregator")
            {
                using var stream = (await sandbox.DownloadSandboxFileAsync(
                    "/mnt/shared/summary/report.json", cancellationToken: cancellationToken)).Value;
                using var reader = new StreamReader(stream);
                var stored = await reader.ReadToEndAsync(cancellationToken);
                var expected = result.StandardOutput!.Split('\n')
                    .Single(item => item.StartsWith("RESULT=", StringComparison.Ordinal))["RESULT=".Length..].Trim();
                if (stored.Trim() != expected)
                {
                    throw new InvalidOperationException("Stored report does not match aggregator stdout.");
                }
            }
            return result.StandardOutput ?? "";
        }
        finally
        {
            await SampleOperations.CleanupAsync($"sandbox {sandbox.Id}",
                token => sandbox.DeleteAsync(cancellationToken: token));
        }
    }
});

static int ReadInt(string name, int fallback, int minimum, int maximum)
{
    var value = Environment.GetEnvironmentVariable(name);
    if (value is null)
    {
        return fallback;
    }
    if (!int.TryParse(value, out var parsed) || parsed < minimum || parsed > maximum)
    {
        throw new ArgumentException($"{name} must be an integer in {minimum}..{maximum}.");
    }
    return parsed;
}
