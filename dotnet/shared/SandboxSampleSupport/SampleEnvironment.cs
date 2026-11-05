using Azure.Identity;
using Microsoft.Azure.ContainerApps.Sandbox;

namespace SandboxSampleSupport;

public static class SampleEnvironment
{
    public static void Load()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory);
             directory is not null; directory = directory.Parent)
        {
            if (directory.Name == "dotnet" &&
                File.Exists(Path.Combine(directory.FullName, "Directory.Build.props")))
            {
                LoadFile(Path.Combine(directory.FullName, "samples", ".env"));
                break;
            }
        }
    }

    public static void LoadFile(string path)
    {
        if (!File.Exists(path))
        {
            return;
        }

        foreach (var rawLine in File.ReadLines(path))
        {
            var line = rawLine.Trim();
            if (line.StartsWith('#') || !line.Contains('='))
            {
                continue;
            }

            var parts = line.Split('=', 2);
            var key = parts[0].Trim();
            if (key.Length > 0 && Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, parts[1].Trim().Trim('"', '\''));
            }
        }
    }

    public static SandboxGroupClient CreateClient()
    {
        Load();
        string[] keys =
        [
            "ACA_SANDBOXGROUP_REGION", "AZURE_SUBSCRIPTION_ID",
            "ACA_RESOURCE_GROUP", "ACA_SANDBOX_GROUP"
        ];
        var missing = keys.Where(key => string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(key))).ToArray();
        if (missing.Length > 0)
        {
            throw new InvalidOperationException(
                $"Missing configuration: {string.Join(", ", missing)}. " +
                @"Run dotnet\setup\setup.ps1 or set the environment variables.");
        }

        var options = new SandboxGroupClientOptions();
        options.Retry.NetworkTimeout = TimeSpan.FromMinutes(10);
        return new SandboxGroupClient(
            Environment.GetEnvironmentVariable(keys[0])!,
            Environment.GetEnvironmentVariable(keys[1])!,
            Environment.GetEnvironmentVariable(keys[2])!,
            Environment.GetEnvironmentVariable(keys[3])!,
            new DefaultAzureCredential(),
            options);
    }
}
