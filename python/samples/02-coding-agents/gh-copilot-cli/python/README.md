# Coding agents — Copilot CLI (Python)

```bash
pip install -r requirements.txt
python copilot.py
```

The script provisions a sandbox, installs Copilot CLI, applies an
egress policy with placeholder Transform rules, and prints the
[sandboxes.azure.com](https://sandboxes.azure.com) URL where you
paste your GitHub PAT and run `copilot` from the portal's bash tab.

Press Enter in this terminal when done to delete the sandbox. See
[../README.md](../README.md) for Copilot-specific notes (PAT scopes,
hosts) and [../../README.md](../../README.md) for the scenario-level
concepts, architecture diagram, and threat model.
