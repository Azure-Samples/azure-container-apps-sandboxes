# Coding agents: Copilot CLI (CLI)

```bash
./run.sh
```

The script provisions a sandbox, installs Copilot CLI, applies
`policy.yaml` (deny-default + GitHub host allows + three placeholder
Transform rules), and prints the
[sandboxes.azure.com](https://sandboxes.azure.com) URL where you paste
your GitHub PAT.

Once you've pasted and pressed Enter, the script opens an
**interactive shell inside the sandbox** (`aca sandbox shell`). Run
`copilot` from there; type `exit` to leave the shell and the script
will delete the sandbox.

If you'd rather stay in the portal, you can use the sandbox page's
`bash` tab and just press Enter in this terminal when done, then
`exit` immediately out of the local shell.

`policy.yaml` is checked in verbatim, it contains no secrets, only
the literal placeholder `PASTE_PAT_HERE` in each Transform rule's
Value. See [../README.md](../README.md) for Copilot-specific notes
(PAT scopes, hosts) and [../../README.md](../../README.md) for the
scenario-level concepts, architecture diagram, and threat model.

## Requirements

- `aca` (installed by `python/samples/setup/setup.py`)
- `python3` or `python` (used to parse `aca sandbox list -o json`; auto-detected at runtime)
- `timeout` (POSIX standard)
