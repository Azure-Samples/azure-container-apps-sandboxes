"""Coding agents — Copilot CLI inside a sandbox (portal-paste flow).

Boots an ubuntu sandbox, installs the GitHub Copilot CLI, and applies a
deny-by-default egress policy with three PAT-injection Transform rules
whose Authorization values are deliberately left as the placeholder
``PASTE_PAT_HERE``. The customer then opens the sandbox in
https://sandboxes.azure.com, drops their GitHub PAT into the three
Value fields, and uses the portal's bash tab to run ``copilot``.

The PAT never enters this script, the env, the shell, or any file on
the operator's disk — it lives only in the customer's browser session
and the ACA control plane.

  python copilot.py

Reads sandbox-group configuration from ``samples/.env``
(written by ``python/samples/setup/setup.py``).
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.containerapps.sandbox import (
    EgressHeader,
    SandboxGroupClient,
    endpoint_for_region,
)


# Host families Copilot CLI talks to. The three hosts in INJECT_HOSTS
# are handled by Transform rules instead — adding them as host rules
# too would short-circuit the transforms and disable PAT injection.
ALLOW_HOSTS = (
    "*.github.com",
    "*.githubusercontent.com",
    "gh.io",
    "*.github.io",
)

# host -> auth header scheme, rule name. The literal "PASTE_PAT_HERE"
# string is what the customer replaces in the portal.
PLACEHOLDER = "PASTE_PAT_HERE"
INJECT_HOSTS: tuple[tuple[str, str, str], ...] = (
    ("api.github.com",                         "token",  "github-api-auth"),
    ("api.enterprise.githubcopilot.com",       "Bearer", "copilot-enterprise-auth"),
    ("telemetry.enterprise.githubcopilot.com", "Bearer", "copilot-telemetry-auth"),
)


def _load_env() -> None:
    """Walk up from this script to find samples/.env and load it."""
    for parent in Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break
    if not os.environ.get("ACA_SANDBOXGROUP_REGION"):
        sys.exit(
            "error: samples/.env is missing required keys. Run:\n"
            "       python python/samples/setup/setup.py"
        )


def _wait_until(predicate, *, timeout: float = 30.0, interval: float = 2.0,
                label: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError(f"timed out waiting for {label} after {timeout:.0f}s")


def _assert_no_copilot_host_rule(sandbox) -> None:
    """A host rule for *.githubcopilot.com would short-circuit our
    Transform rules and the request would go out without the injected
    Authorization header."""
    policy = sandbox.get_egress_policy()
    for rule in getattr(policy, "host_rules", []) or []:
        pattern = getattr(rule, "pattern", "") or ""
        if "githubcopilot.com" in pattern:
            raise RuntimeError(
                "egress policy has a host rule matching *.githubcopilot.com; "
                "this disables the PAT-injection transforms. Remove it."
            )


def _portal_url(sub: str, rg: str, sg: str, sandbox_id: str) -> str:
    return (
        f"https://sandboxes.azure.com/sandbox-groups/{sub}/{rg}/{sg}"
        f"/sandboxes/{sandbox_id}"
    )


def main() -> int:
    _load_env()

    sub = os.environ["AZURE_SUBSCRIPTION_ID"]
    rg = os.environ["ACA_RESOURCE_GROUP"]
    sg = os.environ["ACA_SANDBOX_GROUP"]
    region = os.environ["ACA_SANDBOXGROUP_REGION"]

    run_id = uuid.uuid4().hex[:8]
    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(region),
        credential,
        subscription_id=sub,
        resource_group=rg,
        sandbox_group=sg,
    )

    sandbox = None
    labels = {"scenario": "coding-agents", "run": run_id}
    try:
        print(f"==> Booting sandbox (run={run_id})...")
        # cpu/memory mirror the reference test — Copilot CLI install is
        # heavier than the SDK defaults.
        sandbox = client.begin_create_sandbox(
            disk="ubuntu", cpu="2000m", memory="4096Mi", labels=labels,
        ).result()
        print(f"    sandbox: {sandbox.sandbox_id}")

        print("==> Waiting for sandbox exec to come up...")
        _wait_until(
            lambda: sandbox.exec("true").exit_code == 0,
            label="exec readiness",
        )

        print("==> Installing GitHub Copilot CLI (under default-allow egress)...")
        r = sandbox.exec(
            "timeout 180s bash -lc 'curl -fsSL https://gh.io/copilot-install | bash'"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"copilot install failed: exit={r.exit_code} stderr={r.stderr[:200]!r}"
            )

        print("==> Locking down egress: default = Deny...")
        sandbox.set_egress_default("Deny")

        print(f"==> Allowing GitHub host families ({len(ALLOW_HOSTS)} rules)...")
        for host in ALLOW_HOSTS:
            sandbox.add_egress_host_rule(host, action="Allow")

        print(f"==> Adding PAT-injection transform rules with placeholder values ({len(INJECT_HOSTS)} rules)...")
        for host, scheme, name in INJECT_HOSTS:
            sandbox.add_egress_transform_rule(
                host=host,
                headers=[EgressHeader(
                    operation="Set",
                    name="Authorization",
                    value=f"{scheme} {PLACEHOLDER}",
                )],
                name=name,
            )

        print("==> Verifying no githubcopilot.com host rule exists (footgun guard)...")
        _assert_no_copilot_host_rule(sandbox)

        url = _portal_url(sub, rg, sg, sandbox.sandbox_id)
        print()
        print("=" * 72)
        print("Sandbox is ready. To finish setup, drop your GitHub PAT in the portal:")
        print()
        print(f"  1. Open: {url}")
        print("  2. Click 'Egress Policy' (right-hand panel).")
        print(f"  3. For each of the 3 Transform rules, replace '{PLACEHOLDER}'")
        print("     in the Value field with your GitHub PAT. Keep the scheme")
        print("     prefix ('Bearer' or 'token'). Click Save.")
        print("     -> Need a PAT? Run 'gh auth token' if you already use the gh CLI,")
        print("        or create a classic PAT at:")
        print("        https://github.com/settings/tokens/new?scopes=read:user,repo,workflow")
        print("  4. Open the 'bash' tab on the sandbox page and run:")
        print("       copilot")
        print()
        print("Verification (optional, from the bash tab):")
        print("  curl -i https://api.github.com/user | head")
        print("  -> 200 + your GitHub login proves the api.github.com rule worked.")
        print("     The real end-to-end check is running 'copilot' itself.")
        print()
        print("After paste: do NOT screenshot/share the Egress Policy panel")
        print("(the saved Values contain your PAT verbatim).")
        print("=" * 72)
        print()

        try:
            input("Press Enter here to delete the sandbox when you're done... ")
        except (EOFError, KeyboardInterrupt):
            print()

        return 0

    finally:
        if sandbox is not None:
            print(f"==> Deleting sandbox {sandbox.sandbox_id}...")
            try:
                sandbox.delete()
            except Exception as e:
                print(f"    warning: delete failed: {e}")
        else:
            # Interrupted before begin_create returned — sweep by label.
            print(f"==> Sweeping any leaked sandboxes with run={run_id}...")
            try:
                for sbx in client.list_sandboxes(labels={"run": run_id}):
                    try:
                        client.delete_sandbox(sbx.id)
                        print(f"    deleted leaked sandbox {sbx.id}")
                    except Exception as e:
                        print(f"    warning: failed to delete {sbx.id}: {e}")
            except Exception as e:
                print(f"    warning: sweep failed: {e}")
        client.close()
        credential.close()


if __name__ == "__main__":
    sys.exit(main())
