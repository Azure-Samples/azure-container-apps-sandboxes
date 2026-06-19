"""Provision a sandbox group and write samples/.env.

Creates a resource group, a sandbox group, and grants the signed-in user
data-plane access. Writes the resulting configuration to samples/.env so
all samples can find it.

Prerequisites:
  - Azure CLI installed and logged in (az login)
  - Python 3.10+
  - pip install azure-containerapps-sandbox azure-mgmt-resource azure-mgmt-authorization azure-identity

Usage:
  python python/samples/setup/setup.py [--resource-group my-rg] [--sandbox-group my-sandbox-group] [--region eastus2]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

try:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.containerapps.sandbox import SandboxGroupManagementClient
except ImportError:
    sys.exit(
        "Missing dependencies. Install them with:\n"
        "  pip install azure-containerapps-sandbox azure-mgmt-resource "
        "azure-mgmt-authorization azure-identity"
    )


# Container Apps SandboxGroup Data Owner
ROLE_DEF_ID = "c24cf47c-5077-412d-a19c-45202126392c"


def get_signed_in_user_id(credential) -> str:
    """Get the object ID of the currently signed-in user via az CLI."""
    import subprocess
    import json

    result = subprocess.run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Failed to get signed-in user: {result.stderr.strip()}")
    return result.stdout.strip()


def get_subscription_id() -> str:
    """Get the active subscription ID from az CLI."""
    import subprocess

    result = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Failed to get subscription ID: {result.stderr.strip()}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="Provision ACA Sandboxes sample environment")
    parser.add_argument("--resource-group", default="aca-sandboxes-samples-rg", help="Resource group name")
    parser.add_argument("--sandbox-group", default="aca-sandboxes-samples", help="Sandbox group name")
    parser.add_argument("--region", default="eastus2", help="Azure region")
    parser.add_argument("--subscription-id", default=None, help="Azure subscription ID (default: active az CLI subscription)")
    args = parser.parse_args()

    subscription_id = args.subscription_id or get_subscription_id()
    principal_id = get_signed_in_user_id(None)

    print(f"Subscription: {subscription_id}")
    print(f"Region:       {args.region}")
    print(f"RG:           {args.resource_group}")
    print(f"Sandbox group:{args.sandbox_group}")
    print(f"Principal:    {principal_id}")
    print()

    credential = DefaultAzureCredential()

    # 1. Create resource group
    print("Creating resource group...")
    ResourceManagementClient(credential, subscription_id).resource_groups.create_or_update(
        args.resource_group, {"location": args.region}
    )

    # 2. Create sandbox group
    print("Creating sandbox group...")
    SandboxGroupManagementClient(
        credential, subscription_id=subscription_id, resource_group=args.resource_group
    ).create_group(args.sandbox_group, location=args.region)

    # 3. Grant data-plane role
    print("Granting Container Apps SandboxGroup Data Owner role...")
    scope = (
        f"/subscriptions/{subscription_id}/resourceGroups/{args.resource_group}"
        f"/providers/Microsoft.App/sandboxGroups/{args.sandbox_group}"
    )
    try:
        AuthorizationManagementClient(credential, subscription_id).role_assignments.create(
            scope=scope,
            role_assignment_name=str(uuid.uuid4()),
            parameters={
                "properties": {
                    "roleDefinitionId": (
                        f"/subscriptions/{subscription_id}"
                        f"/providers/Microsoft.Authorization/roleDefinitions/{ROLE_DEF_ID}"
                    ),
                    "principalId": principal_id,
                    "principalType": "User",
                }
            },
        )
    except Exception as e:
        if "RoleAssignmentExists" in str(e):
            print("  (role already assigned, skipping)")
        else:
            raise

    # 4. Write samples/.env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env_content = (
        f"AZURE_SUBSCRIPTION_ID={subscription_id}\n"
        f"ACA_RESOURCE_GROUP={args.resource_group}\n"
        f"ACA_SANDBOX_GROUP={args.sandbox_group}\n"
        f"ACA_SANDBOXGROUP_REGION={args.region}\n"
    )
    env_path.write_text(env_content)
    print(f"\nWrote {env_path}")
    print("\nSetup complete. Role assignments take 30-60 seconds to propagate.")
    print("If samples return 403, wait a minute and retry.")


if __name__ == "__main__":
    main()
