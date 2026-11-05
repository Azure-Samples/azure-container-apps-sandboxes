using DataPipeline;
using NUnit.Framework;

namespace SandboxSamples.Tests;

public class PipelineTests
{
    [Test]
    public async Task ConcurrentPipelinePreservesEventsAndPersistsReport()
    {
        var root = Path.Combine(Path.GetTempPath(), $"dotnet-pipeline-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(root);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await Task.WhenAll(
                Pipeline.TransformAsync(root, 3, timeout.Token),
                Pipeline.ProduceAsync(root, 3, 10, 10, 42, timeout.Token));
            var report = Pipeline.Aggregate(root);
            Assert.That(report["files_read"]!.GetValue<int>(), Is.EqualTo(3));
            Assert.That(report["events_total"]!.GetValue<int>(), Is.EqualTo(30));
            Assert.That(File.ReadAllText(Path.Combine(root, "summary", "report.json")), Is.EqualTo(report.ToJsonString()));
            Assert.That(Directory.GetFiles(Path.Combine(root, "raw"), "batch-*.jsonl"), Is.Empty);
            Assert.That(Directory.GetFiles(Path.Combine(root, "raw", ".done"), "batch-*.jsonl"), Has.Length.EqualTo(3));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Test]
    public async Task AggregationRejectsLostOrDuplicatedEvents()
    {
        var root = Path.Combine(Path.GetTempPath(), $"dotnet-pipeline-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(root);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await Pipeline.ProduceAsync(root, 2, 4, 0, 42, timeout.Token);
            await Pipeline.TransformAsync(root, 2, timeout.Token);
            var first = Path.Combine(root, "processed", "batch-000.jsonl");
            var second = Path.Combine(root, "processed", "batch-001.jsonl");
            var original = File.ReadAllText(second);
            File.Copy(first, second, overwrite: true);
            Assert.Throws<InvalidOperationException>(() => Pipeline.Aggregate(root));
            File.WriteAllText(second, original);
            File.Delete(first);
            Assert.Throws<InvalidOperationException>(() => Pipeline.Aggregate(root));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
