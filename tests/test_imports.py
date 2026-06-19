"""Offline gate: the dependency closure and editable wiring resolve.

These two imports prove the SDK and the 08 editable extension package
are installed and importable. We deliberately do NOT import every sample
script: many are not import-safe (they run work at module load or under
__main__ guards that expect Azure state).
"""


def test_sdk_package_exposes_public_names():
    import azure.containerapps.sandbox as sdk

    for name in (
        "SandboxGroupClient",
        "endpoint_for_region",
        "SandboxGroupManagementClient",
        "EgressHeader",
    ):
        assert hasattr(sdk, name), f"missing SDK export: {name}"


def test_editable_agents_extension_imports():
    import agents_aca_sandboxes

    assert agents_aca_sandboxes.__name__ == "agents_aca_sandboxes"
