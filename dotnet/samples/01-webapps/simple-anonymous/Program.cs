using System.Diagnostics;
using System.Text.Json;
using Microsoft.Azure.ContainerApps.Sandbox.Models;
using SandboxSampleSupport;

return await SampleOperations.RunAsync(async cancellationToken =>
{
    var group = SampleEnvironment.CreateClient();
    var disk = Environment.GetEnvironmentVariable("ACA_WEBAPP_DISK") ?? "node-22";
    var sandbox = (await group.CreateSandboxAsync(
        SampleOperations.FromPublicDisk(disk, "01-webapps"), cancellationToken)).Value;
    Console.WriteLine($"Sandbox: {sandbox.Id}");
    try
    {
        await SampleOperations.WaitForRunningAsync(sandbox, cancellationToken);
        await SampleOperations.UploadAsync(
            sandbox, Path.Combine(AppContext.BaseDirectory, "app", "server.js"),
            "/app/server.js", cancellationToken);
        await SampleOperations.RunShellAsync(sandbox,
            "nohup node /app/server.js > /tmp/node.log 2>&1 < /dev/null & " +
            "i=0; until curl -fsS --max-time 2 http://127.0.0.1:8080/healthz; do " +
            "i=$((i+1)); if [ \"$i\" -ge 30 ]; then cat /tmp/node.log; exit 1; fi; sleep 1; done",
            cancellationToken);

        var ports = (await sandbox.AddPortAsync(
            new CreateSandboxPortContent(8080)
            {
                Name = "sample-http",
                Auth = new PortAuthConfig { Anonymous = true }
            }, cancellationToken)).Value;
        var url = ports.Ports.Single(port => port.Port == 8080).Url
            ?? throw new InvalidOperationException("The service did not return a public URL.");
        Console.WriteLine($"Temporary anonymous public URL: {url}");

        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        foreach (var path in new[] { "/healthz", "/api/hello", "/api/info" })
        {
            var body = await ReadPublicAsync(http, new Uri(url, path), cancellationToken);
            using var json = JsonDocument.Parse(body);
            var root = json.RootElement;
            var valid = path switch
            {
                "/healthz" => root.GetProperty("status").GetString() == "ok",
                "/api/hello" => root.GetProperty("message").GetString() == "Hello from sandbox" &&
                                root.TryGetProperty("hostname", out _) && root.TryGetProperty("uptime", out _),
                _ => root.TryGetProperty("node", out _) && root.TryGetProperty("platform", out _)
            };
            if (!valid)
            {
                throw new InvalidOperationException($"Unexpected response from {path}: {body}");
            }
            Console.WriteLine($"GET {path}: {body}");
        }

        var html = await ReadPublicAsync(http, url, cancellationToken);
        if (!html.Contains("Hello from a sandbox", StringComparison.Ordinal))
        {
            throw new InvalidOperationException("The landing page did not contain the expected greeting.");
        }
        Console.WriteLine("All endpoint checks passed. The temporary sandbox will now be deleted.");
        return 0;
    }
    finally
    {
        await SampleOperations.CleanupAsync($"sandbox {sandbox.Id}",
            token => sandbox.DeleteAsync(cancellationToken: token));
    }
});

static async Task<string> ReadPublicAsync(HttpClient http, Uri url, CancellationToken cancellationToken)
{
    var timer = Stopwatch.StartNew();
    Exception? lastError = null;
    while (timer.Elapsed < TimeSpan.FromSeconds(60))
    {
        try
        {
            using var response = await http.GetAsync(url, cancellationToken);
            response.EnsureSuccessStatusCode();
            return await response.Content.ReadAsStringAsync(cancellationToken);
        }
        catch (HttpRequestException exception)
        {
            lastError = exception;
        }
        catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            lastError = exception;
        }
        await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken);
    }
    throw new TimeoutException($"Public endpoint {url} did not become ready.", lastError);
}
