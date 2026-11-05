using NUnit.Framework;
using SandboxSampleSupport;

namespace SandboxSamples.Tests;

public class McpProtocolTests
{
    [Test]
    public void ReadsJsonAndSseWhileIgnoringNotifications()
    {
        const string response = """{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}""";
        Assert.That(McpProtocol.ReadResult(response, 1).GetProperty("protocolVersion").GetString(), Is.EqualTo("2025-06-18"));
        var sse = ": keepalive\r\n\r\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\r\n\r\n" +
            $"event: message\r\ndata: {response}\r\n\r\n";
        Assert.That(McpProtocol.ReadResult(sse, 1).GetProperty("protocolVersion").GetString(), Is.EqualTo("2025-06-18"));
    }

    [Test]
    public void RejectsMismatchedIdsAndProtocolErrors()
    {
        Assert.Throws<InvalidOperationException>(() =>
            McpProtocol.ReadResult("""{"jsonrpc":"2.0","id":2,"result":{}}""", 1));
        Assert.Throws<InvalidOperationException>(() =>
            McpProtocol.ReadResult("""{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Unknown method"}}""", 1));
        Assert.Throws<InvalidOperationException>(() =>
            McpProtocol.ReadResult("""{"jsonrpc":"1.0","id":1,"result":{}}""", 1));
    }
}
