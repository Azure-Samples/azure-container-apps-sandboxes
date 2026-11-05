using System.Text.Json.Nodes;

namespace DataPipeline;

public static class Pipeline
{
    public static async Task ProduceAsync(
        string mount, int batches, int eventsPerBatch, int delayMs, int seed, CancellationToken cancellationToken)
    {
        var raw = Path.Combine(mount, "raw");
        Directory.CreateDirectory(raw);
        var random = new Random(seed);
        for (var batch = 0; batch < batches; batch++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var events = Enumerable.Range(0, eventsPerBatch).Select(index =>
            {
                var roll = random.NextDouble();
                var type = roll < .60 ? "page_view" : roll < .85 ? "click" :
                    roll < .95 ? "logout" : roll < .99 ? "purchase" : "signup";
                return new JsonObject
                {
                    ["id"] = $"{batch:D3}-{index:D4}",
                    ["ts"] = 1700000000L + random.Next(7 * 24 * 60 * 60 + 1),
                    ["user"] = $"u{random.Next(100):D4}",
                    ["type"] = type,
                    ["value"] = type == "purchase" ? Math.Round(5 + random.NextDouble() * 495, 2) : 0
                }.ToJsonString();
            });
            WriteAtomic(Path.Combine(raw, $"batch-{batch:D3}.jsonl"), string.Join('\n', events) + "\n");
            Console.WriteLine($"produced batch-{batch:D3}: {eventsPerBatch} events");
            if (batch + 1 < batches)
            {
                await Task.Delay(delayMs, cancellationToken);
            }
        }
        WriteAtomic(Path.Combine(raw, ".producer-done"), checked(batches * eventsPerBatch).ToString());
    }

    public static async Task TransformAsync(string mount, int batches, CancellationToken cancellationToken)
    {
        var raw = Path.Combine(mount, "raw");
        var done = Path.Combine(raw, ".done");
        var processed = Path.Combine(mount, "processed");
        Directory.CreateDirectory(done);
        Directory.CreateDirectory(processed);
        var count = 0;
        // Wait for the expected batch count, not just a momentarily quiet mount.
        while (count < batches || !File.Exists(Path.Combine(raw, ".producer-done")))
        {
            cancellationToken.ThrowIfCancellationRequested();
            foreach (var source in Directory.GetFiles(raw, "batch-*.jsonl").Order())
            {
                var enriched = File.ReadLines(source).Where(line => !string.IsNullOrWhiteSpace(line)).Select(line =>
                {
                    var item = JsonNode.Parse(line)!.AsObject();
                    item["hour"] = DateTimeOffset.FromUnixTimeSeconds(item["ts"]!.GetValue<long>()).Hour;
                    item["revenue"] = item["value"]!.GetValue<double>() > 0;
                    return item.ToJsonString();
                });
                WriteAtomic(Path.Combine(processed, Path.GetFileName(source)), string.Join('\n', enriched) + "\n");
                File.Move(source, Path.Combine(done, Path.GetFileName(source)));
                count++;
                Console.WriteLine($"transformed {Path.GetFileName(source)}");
            }
            await Task.Delay(250, cancellationToken);
        }
        if (count != batches)
        {
            throw new InvalidOperationException($"Expected {batches} batches, transformed {count}.");
        }
    }

    public static JsonObject Aggregate(string mount)
    {
        var files = Directory.GetFiles(Path.Combine(mount, "processed"), "batch-*.jsonl").Order().ToArray();
        var ids = new HashSet<string>();
        var byType = new Dictionary<string, int>();
        var byUser = new Dictionary<string, int>();
        var byHour = new Dictionary<string, int>();
        var revenueEvents = 0;
        double value = 0;
        foreach (var file in files)
        {
            foreach (var line in File.ReadLines(file).Where(line => !string.IsNullOrWhiteSpace(line)))
            {
                var item = JsonNode.Parse(line)!;
                if (!ids.Add(item["id"]!.GetValue<string>()))
                {
                    throw new InvalidOperationException("Duplicate event ID in processed batches.");
                }
                var amount = item["value"]!.GetValue<double>();
                var hour = item["hour"]!.GetValue<int>();
                if (hour is < 0 or > 23 || item["revenue"]!.GetValue<bool>() != (amount > 0))
                {
                    throw new InvalidOperationException("Invalid event enrichment.");
                }
                Increment(byType, item["type"]!.GetValue<string>());
                Increment(byUser, item["user"]!.GetValue<string>());
                Increment(byHour, hour.ToString());
                if (amount > 0)
                {
                    revenueEvents++;
                    value += amount;
                }
            }
        }
        var produced = int.Parse(File.ReadAllText(Path.Combine(mount, "raw", ".producer-done")));
        if (ids.Count != produced)
        {
            throw new InvalidOperationException($"Expected {produced} events, aggregated {ids.Count}.");
        }
        var report = new JsonObject
        {
            ["files_read"] = files.Length,
            ["events_total"] = ids.Count,
            ["revenue_events"] = revenueEvents,
            ["total_value"] = Math.Round(value, 2),
            ["avg_value"] = ids.Count == 0 ? 0 : Math.Round(value / ids.Count, 4),
            ["events_by_type"] = Counts(byType),
            ["top_users"] = Counts(byUser, 10),
            ["top_hours"] = Counts(byHour, 10)
        };
        Directory.CreateDirectory(Path.Combine(mount, "summary"));
        WriteAtomic(Path.Combine(mount, "summary", "report.json"), report.ToJsonString());
        return report;
    }

    private static void Increment(Dictionary<string, int> counts, string key) =>
        counts[key] = counts.GetValueOrDefault(key) + 1;

    private static JsonObject Counts(Dictionary<string, int> counts, int limit = int.MaxValue)
    {
        var result = new JsonObject();
        foreach (var (key, value) in counts.OrderByDescending(item => item.Value).ThenBy(item => item.Key).Take(limit))
        {
            result[key] = value;
        }
        return result;
    }

    private static void WriteAtomic(string path, string content)
    {
        var temporary = path + ".tmp";
        File.WriteAllText(temporary, content);
        File.Move(temporary, path, overwrite: true);
    }
}
