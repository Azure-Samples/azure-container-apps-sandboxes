using System.Text.Json;

namespace SandboxSampleSupport;

public static class McpProtocol
{
    public const string InitializeRequest = """
        {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"aca-dotnet-sample","version":"1.0"}}}
        """;

    public static JsonElement ReadResult(string body, int requestId)
    {
        var trimmed = body.Trim();
        if (trimmed.StartsWith('{'))
        {
            return ReadJson(trimmed, requestId)
                ?? throw new InvalidOperationException("The MCP response ID does not match the request.");
        }

        var data = new List<string>();
        foreach (var line in (body.Replace("\r\n", "\n") + "\n\n").Split('\n'))
        {
            if (line.Length == 0 && data.Count > 0)
            {
                var result = ReadJson(string.Join('\n', data), requestId);
                if (result is not null)
                {
                    return result.Value;
                }
                data.Clear();
            }
            else if (line.StartsWith("data:", StringComparison.Ordinal))
            {
                data.Add(line["data:".Length..].TrimStart(' '));
            }
        }
        throw new InvalidOperationException("No matching MCP JSON-RPC response was received.");
    }

    private static JsonElement? ReadJson(string json, int requestId)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (!root.TryGetProperty("id", out var id) || !id.TryGetInt32(out var number) || number != requestId)
        {
            return null;
        }
        if (root.TryGetProperty("error", out var error))
        {
            throw new InvalidOperationException($"MCP request failed: {error}");
        }
        if (!root.TryGetProperty("jsonrpc", out var version) || version.GetString() != "2.0" ||
            !root.TryGetProperty("result", out var result))
        {
            throw new InvalidOperationException("Invalid MCP JSON-RPC response.");
        }
        return result.Clone();
    }
}
