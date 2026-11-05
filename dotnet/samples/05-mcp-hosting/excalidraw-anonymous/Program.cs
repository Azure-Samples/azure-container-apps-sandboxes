using System.Diagnostics;
using System.Text;
using Microsoft.Azure.ContainerApps.Sandbox.Models;
using SandboxSampleSupport;

return await SampleOperations.RunAsync(async cancellationToken =>
{
    if (args.Length > 1 || (args.Length == 1 && args[0] != "--validate"))
    {
        throw new ArgumentException("Usage: McpHostingSample [--validate]");
    }
    var group = SampleEnvironment.CreateClient();
    var content = SampleOperations.FromPublicDisk(
        Environment.GetEnvironmentVariable("ACA_MCP_DISK") ?? "copilot", "05-mcp-hosting");
    content.Resources = new SandboxResources("2000m", "4096Mi");
    var sandbox = (await group.CreateSandboxAsync(content, cancellationToken)).Value;
    Console.WriteLine($"Sandbox: {sandbox.Id}");
    try
    {
        await SampleOperations.WaitForRunningAsync(sandbox, cancellationToken);
        Console.WriteLine("Cloning, installing dependencies, and building Excalidraw MCP...");
        var build = await SampleOperations.RunShellAsync(sandbox,
            "timeout 600s bash -lc 'set -euo pipefail; " +
            "git clone --depth=1 https://github.com/excalidraw/excalidraw-mcp.git /opt/excalidraw-mcp; " +
            "cd /opt/excalidraw-mcp; git rev-parse HEAD; " +
            "npm install --no-audit --no-fund; npm run build'",
            cancellationToken);
        Console.WriteLine(build.StandardOutput);
        using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(McpProtocol.InitializeRequest)))
        {
            var upload = (await sandbox.WriteSandboxFileAsync(
                "/tmp/mcp-initialize.json", stream, cancellationToken: cancellationToken)).Value;
            if (!upload.Success)
            {
                throw new InvalidOperationException($"MCP probe upload failed: {upload.Error}");
            }
        }
        await SampleOperations.RunShellAsync(sandbox,
            "cd /opt/excalidraw-mcp && " +
            "nohup env PORT=80 node dist/index.js > /tmp/excalidraw.log 2>&1 < /dev/null &",
            cancellationToken);
        var readiness = await SampleOperations.RunShellAsync(sandbox,
            "timeout 90s bash -c 'until curl -fsS --max-time 10 http://127.0.0.1:80/mcp " +
            "-H \"Content-Type: application/json\" -H \"Accept: application/json, text/event-stream\" " +
            "--data-binary @/tmp/mcp-initialize.json > /tmp/mcp-response; " +
            "do sleep 2; done; cat /tmp/mcp-response' || { cat /tmp/excalidraw.log; exit 1; }",
            cancellationToken);
        var local = McpProtocol.ReadResult(readiness.StandardOutput!, 1);
        if (!local.TryGetProperty("protocolVersion", out _))
        {
            throw new InvalidOperationException("Local MCP initialization did not negotiate a protocol version.");
        }
        var ports = (await sandbox.AddPortAsync(new CreateSandboxPortContent(80)
        {
            Name = "excalidraw-mcp",
            Auth = new PortAuthConfig { Anonymous = true }
        }, cancellationToken)).Value;
        var url = ports.Ports.Single(port => port.Port == 80).Url
            ?? throw new InvalidOperationException("No public URL returned for the MCP server.");
        var endpoint = new Uri(url, "/mcp");
        Console.WriteLine($"Temporary public MCP endpoint: {endpoint}");

        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        var timer = Stopwatch.StartNew();
        string? session = null;
        Exception? lastError = null;
        var initialized = false;
        while (timer.Elapsed < TimeSpan.FromSeconds(90))
        {
            try
            {
                using var response = await PostAsync(McpProtocol.InitializeRequest);
                response.EnsureSuccessStatusCode();
                var result = McpProtocol.ReadResult(await response.Content.ReadAsStringAsync(cancellationToken), 1);
                if (!result.TryGetProperty("protocolVersion", out var protocol))
                {
                    throw new InvalidOperationException("Public MCP initialization has no protocol version.");
                }
                session = response.Headers.TryGetValues("Mcp-Session-Id", out var values) ? values.Single() : null;
                http.DefaultRequestHeaders.Add("MCP-Protocol-Version", protocol.GetString());
                Console.WriteLine($"MCP initialize passed: {result}");
                initialized = true;
                break;
            }
            catch (Exception exception) when (exception is HttpRequestException or InvalidOperationException ||
                exception is OperationCanceledException && !cancellationToken.IsCancellationRequested)
            {
                lastError = exception;
            }
            await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken);
        }
        if (!initialized)
        {
            throw new TimeoutException("Public MCP endpoint did not become ready.", lastError);
        }
        if (session is not null)
        {
            http.DefaultRequestHeaders.Add("Mcp-Session-Id", session);
        }
        using (var notification = await PostAsync("""{"jsonrpc":"2.0","method":"notifications/initialized"}"""))
        {
            notification.EnsureSuccessStatusCode();
        }
        using (var response = await PostAsync("""{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}"""))
        {
            response.EnsureSuccessStatusCode();
            var result = McpProtocol.ReadResult(await response.Content.ReadAsStringAsync(cancellationToken), 2);
            var tools = result.GetProperty("tools");
            if (tools.GetArrayLength() == 0)
            {
                throw new InvalidOperationException("Excalidraw returned no MCP tools.");
            }
            Console.WriteLine($"MCP tools/list passed: {string.Join(", ", tools.EnumerateArray().Select(tool => tool.GetProperty("name").GetString()))}");
        }
        if (args.Length == 0)
        {
            Console.WriteLine("The URL is anonymous and public. Use only demo data. Press Enter to delete the sandbox.");
            await Console.In.ReadLineAsync(cancellationToken);
        }
        return 0;

        Task<HttpResponseMessage> PostAsync(string body)
        {
            var request = new HttpRequestMessage(HttpMethod.Post, endpoint)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json")
            };
            request.Headers.Accept.ParseAdd("application/json");
            request.Headers.Accept.ParseAdd("text/event-stream");
            return SendAsync(request);
        }

        async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request)
        {
            using (request)
            {
                return await http.SendAsync(request, cancellationToken);
            }
        }
    }
    finally
    {
        // Deleting the sandbox also removes its public port routing.
        await SampleOperations.CleanupAsync($"sandbox {sandbox.Id}",
            token => sandbox.DeleteAsync(cancellationToken: token));
    }
});
