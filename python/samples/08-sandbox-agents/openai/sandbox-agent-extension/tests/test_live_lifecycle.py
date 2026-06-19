"""Live integration test for the ACA Sandboxes provider.

Skipped unless ``ACA_LIVE_TEST=1`` in the env. Verifies the full lifecycle
against a real Azure sandbox group: create → exec → write → read → delete.

Always labels its sandbox with ``test=m2-live-lifecycle`` so it is trivially
distinguishable from pre-existing demo sandboxes and we never delete other
people's work.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

ACA_LIVE_TEST = os.environ.get("ACA_LIVE_TEST", "").strip().lower() in {"1", "true", "yes"}

pytestmark = pytest.mark.skipif(
    not ACA_LIVE_TEST, reason="Set ACA_LIVE_TEST=1 to enable live Azure-touching test"
)


async def test_full_lifecycle() -> None:
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.identity.aio import DefaultAzureCredential

    from agents_aca_sandboxes import (
        ACASandboxesClient,
        ACASandboxesClientOptions,
        load_config,
    )

    cfg = load_config()
    cred = DefaultAzureCredential()
    sandbox_id: str | None = None

    try:
        async with SandboxGroupClient(
            endpoint=cfg.endpoint,
            credential=cred,
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            sandbox_group=cfg.sandbox_group,
        ) as gc:
            client = ACASandboxesClient(gc)
            options = ACASandboxesClientOptions(
                disk="ubuntu",
                labels={
                    "test": "m2-live-lifecycle",
                    "owner": os.environ.get("USERNAME", "unknown"),
                },
            )

            session = await client.create(options=options)
            try:
                # The inner session holds our extra fields.
                inner = session._inner  # type: ignore[attr-defined]
                sandbox_id = inner.state.sandbox_id
                assert sandbox_id
                print(f"==> Created sandbox: {sandbox_id}")

                # Start the session so the manifest/workspace machinery runs.
                # An empty manifest is the default — no entries to apply.
                await session.start()

                # exec — echo
                result = await session.exec("echo aca && uname -srm")
                assert result.exit_code == 0, result.stderr
                assert b"aca" in result.stdout
                print(f"==> exec OK: {result.stdout!r}")

                # exec with timeout — sleep beyond timeout
                with pytest.raises(Exception):  # ExecTimeoutError
                    await session.exec("sleep 5", timeout=1)
                print("==> timeout OK")

                # write + read round-trip
                target = Path("/workspace/m2_test.txt")
                payload = b"hello from M2 integration test\n"
                await session.write(target, io.BytesIO(payload))
                buf = await session.read(target)
                got = buf.read()
                assert got == payload, (got, payload)
                print(f"==> write/read OK: {got!r}")
            finally:
                await client.delete(session)
                print(f"==> Deleted sandbox: {sandbox_id}")
    finally:
        await cred.close()
