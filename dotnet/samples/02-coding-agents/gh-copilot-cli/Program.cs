using Microsoft.Azure.ContainerApps.Sandbox.Models;
using SandboxSampleSupport;

return await SampleOperations.RunAsync(async cancellationToken =>
{
    if (args.Length > 1 || (args.Length == 1 && args[0] != "--validate"))
    {
        throw new ArgumentException("Usage: CodingAgentSample [--validate]");
    }

    var group = SampleEnvironment.CreateClient();
    var content = SampleOperations.FromPublicDisk("ubuntu", "02-coding-agents");
    content.Resources = new SandboxResources("2000m", "4096Mi");
    var sandbox = (await group.CreateSandboxAsync(content, cancellationToken)).Value;
    Console.WriteLine($"Sandbox: {sandbox.Id}");
    try
    {
        await SampleOperations.WaitForRunningAsync(sandbox, cancellationToken);
        var install = await SampleOperations.RunShellAsync(sandbox,
            "timeout 180s bash -lc 'set -o pipefail; curl -fsSL https://gh.io/copilot-install | bash' && copilot --version",
            cancellationToken);
        Console.WriteLine(install.StandardOutput);

        var policy = new SandboxEgressPolicy
        {
            DefaultAction = EgressPolicyAction.Deny,
            TrafficInspection = TrafficInspection.Full
        };
        foreach (var host in new[] { "*.github.com", "*.githubusercontent.com", "gh.io", "*.github.io" })
        {
            policy.HostRules.Add(new EgressHostRule(host) { Action = EgressPolicyAction.Allow });
        }
        foreach (var (host, scheme, name) in new[]
        {
            ("api.github.com", "token", "github-api-auth"),
            ("api.enterprise.githubcopilot.com", "Bearer", "copilot-enterprise-auth"),
            ("telemetry.enterprise.githubcopilot.com", "Bearer", "copilot-telemetry-auth")
        })
        {
            var action = new EgressPolicyRuleAction(EgressPolicyActionType.Transform);
            action.Headers.Add(new EgressPolicyHeaderTransform(EgressPolicyHeaderOperation.Set, "Authorization")
            {
                Value = $"{scheme} PASTE_PAT_HERE"
            });
            policy.Rules.Add(new EgressPolicyRule(name, new EgressPolicyRuleMatch(host)) { Action = action });
        }
        await sandbox.SetEgressPolicyAsync(policy, cancellationToken);
        var saved = (await sandbox.GetAsync(cancellationToken)).Value.Data?.EgressPolicy
            ?? throw new InvalidOperationException("The sandbox has no egress policy.");
        if (saved.DefaultAction != EgressPolicyAction.Deny || saved.TrafficInspection != TrafficInspection.Full ||
            saved.Rules.Count != 3 || saved.HostRules.Any(rule =>
                rule.Pattern.Contains("githubcopilot.com", StringComparison.OrdinalIgnoreCase)))
        {
            throw new InvalidOperationException("The saved policy does not match the portal-paste configuration.");
        }

        // Allow time for the policy to reach the egress proxy.
        await SampleOperations.RunShellAsync(sandbox,
            "timeout 60s bash -c 'while :; do " +
            "allowed=$(curl -sS -o /dev/null -w \"%{http_code}\" --max-time 10 https://github.com); " +
            "denied=$(curl -sS -o /dev/null -w \"%{http_code}\" --max-time 10 https://example.com); " +
            "if [ \"$allowed\" = 200 ] && [ \"$denied\" = 403 ]; then " +
            "echo \"GitHub allowed; unlisted host denied (403)\"; exit 0; fi; sleep 2; done'",
            cancellationToken);
        Console.WriteLine("Copilot installation and deny-default egress checks passed.");

        if (args.Length == 0)
        {
            Console.WriteLine(
                $"Open https://sandboxes.azure.com/sandbox-groups/{group.SubscriptionId}/" +
                $"{group.ResourceGroupName}/{group.Name}/sandboxes/{sandbox.Id}");
            Console.WriteLine("In Egress Policy, replace PASTE_PAT_HERE in all three Transform values with your PAT.");
            Console.WriteLine("Keep each token/Bearer prefix. Never share the saved policy or put your PAT in this script.");
            Console.WriteLine("Open the portal bash tab and run copilot. Press Enter here to delete the sandbox.");
            await Console.In.ReadLineAsync(cancellationToken);
        }
        else
        {
            Console.WriteLine("Validation does not test authenticated Copilot chat; portal PAT setup remains manual.");
        }
        return 0;
    }
    finally
    {
        await SampleOperations.CleanupAsync($"sandbox {sandbox.Id}",
            token => sandbox.DeleteAsync(cancellationToken: token));
    }
});
