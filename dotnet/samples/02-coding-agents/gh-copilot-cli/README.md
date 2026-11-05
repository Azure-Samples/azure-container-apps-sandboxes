# 02: Copilot CLI with deny-default egress (.NET)

Ports the Python `02-coding-agents/gh-copilot-cli` portal-paste flow.
The host uses the Sandbox .NET SDK; no Python runtime is needed.

Complete the [SDK and resource setup](../../../README.md), then run from the repository root:

```powershell
dotnet run --project dotnet\samples\02-coding-agents\gh-copilot-cli
```

The sample installs Copilot CLI in an Ubuntu sandbox, enables full traffic
inspection, and applies deny-default egress with four GitHub host rules and three
Authorization transforms containing `PASTE_PAT_HERE`. It reads the saved policy
back and checks that GitHub is reachable while an unlisted host returns 403.
Do not add a `*.githubcopilot.com` host rule: it bypasses the authentication
transforms.

Open the printed portal link, replace the three placeholder values with your
GitHub PAT (preserving the `token`/`Bearer` prefix), then run `copilot` in the
portal bash tab. The PAT is entered only in the portal, never in the sample.
Do not share screenshots of the saved policy. Press Enter to delete the sandbox.
The sample also attempts cleanup on errors, cancellation, and its 20-minute timeout.

For unattended live validation with automatic cleanup:

```powershell
dotnet run --project dotnet\samples\02-coding-agents\gh-copilot-cli -- --validate
```

This verifies the installation, saved policy, and live allow/deny behavior.
It does **not** validate authenticated Copilot chat, which requires the manual
portal step and a Copilot-enabled account. Exit 0 means setup validation passed;
exit 1 means an operation or cleanup failed.
