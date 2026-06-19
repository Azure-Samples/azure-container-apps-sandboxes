"""ACA Sandboxes provider implementation.

Bridges the `OpenAI Agents SDK <https://github.com/openai/openai-agents-python>`_
sandbox contract (``BaseSandboxClient`` / ``BaseSandboxSession``) onto the
``azure-containerapps-sandbox`` async data-plane client.

Lifecycle
---------
``ACASandboxesClient.create`` calls
:meth:`SandboxGroupClient.begin_create_sandbox` and waits for the LRO. The
poller resolves to a :class:`SandboxClient` already bound to the running
sandbox; we attach that client to the inner :class:`ACASandboxesSession` and
let the base class drive workspace materialization through our IO primitives.

``ACASandboxesClient.delete`` mirrors the upstream Docker provider: shut down
the inner session (closes dependencies, runs hooks) and then call
:meth:`SandboxClient.begin_delete` to release the backend microVM.

``ACASandboxesClient.resume`` reattaches to the sandbox identified by
``state.sandbox_id`` via :meth:`SandboxGroupClient.get_sandbox_client` so
serialized sessions can pick up exactly where they left off.

IO primitives
-------------
``_exec_internal`` joins the command parts with :func:`shlex.join` and hands
the shell string to :meth:`SandboxClient.exec`, then re-encodes the resulting
text streams as ``bytes`` for the OpenAI Agents SDK's :class:`ExecResult`.

``read``/``write`` map onto :meth:`SandboxClient.read_file` and
:meth:`SandboxClient.write_file`. We accept any ``io.IOBase`` on the write
side and return :class:`io.BytesIO` on the read side, matching the upstream
Docker provider's contract.

Snapshot/volume support
-----------------------
``persist_workspace``/``hydrate_workspace`` raise :class:`NotImplementedError`
in the M2 cut; the deep-research demo uses ``NoopSnapshot`` so workspace
materialization happens entirely through the manifest each session start.
Snapshot warm boot is wired in M5 using ACA's native :meth:`create_snapshot`
+ ``snapshot_id`` create path.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shlex
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agents.sandbox.errors import ExecTimeoutError, ExecTransportError
from agents.sandbox.manifest import Manifest
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.dependencies import Dependencies
from agents.sandbox.session.manager import Instrumentation
from agents.sandbox.session.sandbox_client import BaseSandboxClient
from agents.sandbox.session.sandbox_session import SandboxSession
from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec, resolve_snapshot
from agents.sandbox.types import ExecResult, ExposedPortEndpoint, User

from ._exceptions import remap_exception
from ._paths import to_posix
from .models import ACASandboxesClientOptions, ACASandboxesSessionState

if TYPE_CHECKING:  # pragma: no cover — type-only import to avoid runtime cost
    from azure.containerapps.sandbox.aio import SandboxClient, SandboxGroupClient

logger = logging.getLogger(__name__)


def _ensure_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


class ACASandboxesSession(BaseSandboxSession):
    """Provider-side sandbox session backed by an :class:`aio.SandboxClient`."""

    state: ACASandboxesSessionState

    def __init__(
        self,
        *,
        state: ACASandboxesSessionState,
        sandbox_client: "SandboxClient | None" = None,
        group_client: "SandboxGroupClient | None" = None,
    ) -> None:
        self.state = state
        # When create() is the entry point the sandbox client comes straight
        # from the LRO poller. When resume() is the entry point we hold a
        # reference to the group client so we can lazily recreate the
        # SandboxClient if the network handle expires.
        self._sb = sandbox_client
        self._gc = group_client
        self._running = sandbox_client is not None

    # ------------------------------------------------------------ factories

    @classmethod
    def from_state(cls, state: ACASandboxesSessionState) -> "ACASandboxesSession":
        return cls(state=state)

    # ------------------------------------------------------------ capabilities

    def supports_pty(self) -> bool:
        return False

    def supports_docker_volume_mounts(self) -> bool:
        return False

    # ------------------------------------------------------------ lifecycle

    async def running(self) -> bool:
        return self._running and self._sb is not None

    async def _prepare_backend_workspace(self) -> None:
        # ACA sandboxes come up with /workspace already mounted by the runtime
        # disk; we just make sure /workspace exists in case the disk is bare.
        if self._sb is None:
            return
        try:
            await self._sb.mkdir(to_posix("/workspace"))
        except Exception as e:  # noqa: BLE001 — best-effort, exec layer logs
            logger.debug("_prepare_backend_workspace mkdir: %s", e)

    async def _after_start(self) -> None:
        self._running = True

    async def _after_start_failed(self) -> None:
        self._running = False

    async def _after_shutdown(self) -> None:
        # IMPORTANT: do NOT delete the sandbox here. The base class calls
        # ``_after_shutdown`` whenever the local session closes (which can be
        # repeatedly across resume cycles) — backend cleanup belongs in
        # ``ACASandboxesClient.delete`` so users can ``resume`` later.
        self._running = False

    async def _resolve_exposed_port(self, port: int) -> ExposedPortEndpoint:
        if self._sb is None:
            raise ExecTransportError(
                command=[],
                message="Sandbox client is not bound; call create() first.",
            )
        try:
            published = await self._sb.add_port(port)
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(e, context=f"add_port({port})") from e
        host = getattr(published, "host", None) or getattr(published, "url", "")
        return ExposedPortEndpoint(host=host, port=port)

    # ------------------------------------------------------------ IO

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: float | None = None,
    ) -> ExecResult:
        if self._sb is None:
            raise ExecTransportError(
                command=list(command),
                message="Sandbox client is not bound; call create() first.",
            )

        joined = command[0] if len(command) == 1 else shlex.join(str(c) for c in command)
        coro = self._sb.exec(str(joined))
        try:
            result = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
        except asyncio.TimeoutError as e:
            raise ExecTimeoutError(
                command=list(command),
                timeout_s=timeout,
                cause=e,
            ) from e
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(
                e,
                context=f"exec({str(joined)[:120]!r})",
                command=list(command),
            ) from e

        return ExecResult(
            stdout=_ensure_bytes(result.stdout),
            stderr=_ensure_bytes(result.stderr),
            exit_code=int(result.exit_code or 0),
        )

    async def read(self, path: Path, *, user: str | User | None = None) -> io.IOBase:
        if self._sb is None:
            raise ExecTransportError(
                command=[],
                message="Sandbox client is not bound; call create() first.",
            )
        target = to_posix(path)
        try:
            data = await self._sb.read_file(target)
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(
                e, context=f"read_file({target!r})", path=target
            ) from e
        return io.BytesIO(data if isinstance(data, bytes) else _ensure_bytes(data))

    async def write(
        self,
        path: Path,
        data: io.IOBase,
        *,
        user: str | User | None = None,
    ) -> None:
        if self._sb is None:
            raise ExecTransportError(
                command=[],
                message="Sandbox client is not bound; call create() first.",
            )
        target = to_posix(path)
        payload = data.read()
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            await self._sb.write_file(target, payload, create_dirs=True)
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(
                e, context=f"write_file({target!r})", path=target
            ) from e

    # ------------------------------------------------------------ workspace

    async def persist_workspace(self) -> io.IOBase:
        # M5 will wire snapshot warm boot through ACA's native ``create_snapshot``
        # + ``snapshot_id`` create path so we never round-trip a tar archive
        # through the harness. For M2 we return an empty buffer so the
        # framework's snapshot bookkeeping does not blow up the cleanup path
        # when the runtime opportunistically persists state at session close.
        return io.BytesIO(b"")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        # Counterpart to ``persist_workspace``: no-op in M2. Workspace state
        # is rematerialised from the ``Manifest`` each session start.
        try:
            data.read()  # drain so callers can free the buffer
        except Exception:  # noqa: BLE001 — best-effort drain
            pass
        return None


class ACASandboxesClient(BaseSandboxClient[ACASandboxesClientOptions]):
    """Provider client backed by an ``aio.SandboxGroupClient`` transport handle."""

    backend_id: str = "aca_sandboxes"
    supports_default_options: bool = True

    def __init__(
        self,
        group_client: "SandboxGroupClient",
        *,
        instrumentation: Instrumentation | None = None,
        dependencies: Dependencies | None = None,
    ) -> None:
        super().__init__()
        self._group_client = group_client
        self._instrumentation = instrumentation or Instrumentation()
        self._dependencies = dependencies

    @property
    def group_client(self) -> "SandboxGroupClient":
        return self._group_client

    # ------------------------------------------------------------ create

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: ACASandboxesClientOptions | None = None,
    ) -> SandboxSession:
        opts = options or ACASandboxesClientOptions()
        manifest = manifest or Manifest()
        session_id = uuid.uuid4()
        snapshot_instance = resolve_snapshot(snapshot, str(session_id))
        labels = opts.merged_labels()
        labels.setdefault("session-id", str(session_id))

        # The Azure SDK rejects labels, environment, connections, egress_policy,
        # volumes, ports, entrypoint, cmd, skip_egress_proxy, and vmm_type when
        # snapshot_id is set — snapshot restore replays captured state as-is.
        # We strip those here so callers can pass the same options regardless
        # of source. The session-id label is preserved in ``state.session_id``
        # for observability.
        restoring_from_snapshot = opts.snapshot_id is not None

        create_kwargs: dict[str, object] = {
            "polling_interval": int(opts.polling_interval_seconds),
            "polling_timeout": int(opts.polling_timeout_seconds),
        }
        if restoring_from_snapshot:
            create_kwargs["snapshot_id"] = opts.snapshot_id
        else:
            create_kwargs["labels"] = labels
            if opts.disk_id is not None:
                create_kwargs["disk_id"] = opts.disk_id
            else:
                create_kwargs["disk"] = opts.disk
            if opts.cpu is not None:
                create_kwargs["cpu"] = opts.cpu
            if opts.memory is not None:
                create_kwargs["memory"] = opts.memory
            if opts.exposed_ports:
                create_kwargs["ports"] = list(opts.exposed_ports)
            if opts.volume_mounts:
                from azure.containerapps.sandbox import SandboxVolume

                create_kwargs["volumes"] = [
                    SandboxVolume(
                        volume_name=mount.volume_name,
                        mountpoint=mount.mountpoint,
                        read_only=mount.read_only,
                    )
                    for mount in opts.volume_mounts
                ]

        try:
            poller = await self._group_client.begin_create_sandbox(**create_kwargs)
            sandbox_client: "SandboxClient" = await poller.result()
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(e, context="begin_create_sandbox") from e

        gc_subscription = getattr(self._group_client, "subscription_id", "")
        gc_resource_group = getattr(self._group_client, "resource_group", "")
        gc_sandbox_group = getattr(self._group_client, "sandbox_group", "")
        gc_region = self._region_from_endpoint()

        state = ACASandboxesSessionState(
            type="aca_sandboxes",
            session_id=session_id,
            manifest=manifest,
            snapshot=snapshot_instance,
            exposed_ports=opts.exposed_ports,
            sandbox_id=sandbox_client.sandbox_id,
            sandbox_group=gc_sandbox_group,
            resource_group=gc_resource_group,
            subscription_id=gc_subscription,
            region=gc_region,
        )

        inner = ACASandboxesSession(
            state=state,
            sandbox_client=sandbox_client,
            group_client=self._group_client,
        )
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    # ------------------------------------------------------------ delete

    async def delete(self, session: SandboxSession) -> SandboxSession:
        inner = session._inner
        if not isinstance(inner, ACASandboxesSession):
            raise TypeError("ACASandboxesClient.delete expects an ACASandboxesSession")

        # Run local shutdown hooks first so dependencies/streams close cleanly,
        # then tear down the backend microVM via the LRO.
        try:
            await inner.shutdown()
        except Exception as e:  # noqa: BLE001 — best-effort local teardown
            logger.debug("inner.shutdown() during delete: %s", e)

        sandbox_id = inner.state.sandbox_id
        try:
            poller = await self._group_client.begin_delete_sandbox(sandbox_id)
            await poller.result()
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(e, context=f"begin_delete_sandbox({sandbox_id!r})") from e

        # Drop references; the SandboxClient handle owns its own httpx pool.
        inner._sb = None
        inner._running = False
        return session

    # ------------------------------------------------------------ resume

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        if not isinstance(state, ACASandboxesSessionState):
            raise TypeError("ACASandboxesClient.resume expects an ACASandboxesSessionState")

        try:
            sandbox_client = self._group_client.get_sandbox_client(state.sandbox_id)
            # Wake the sandbox if it was auto-suspended; harmless if already running.
            await sandbox_client.ensure_running()
        except Exception as e:  # noqa: BLE001 — remap to SDK-native error
            raise remap_exception(
                e, context=f"resume(sandbox_id={state.sandbox_id!r})"
            ) from e

        inner = ACASandboxesSession(
            state=state,
            sandbox_client=sandbox_client,
            group_client=self._group_client,
        )
        # Tell the base class we're resuming preserved backend state so it can
        # skip a full manifest re-apply if the workspace is still ready.
        inner._set_start_state_preserved(True)
        return self._wrap_session(inner, instrumentation=self._instrumentation)

    def deserialize_session_state(self, payload: dict[str, object]) -> SandboxSessionState:
        instance = ACASandboxesSessionState.model_validate(payload)
        return cast(SandboxSessionState, instance)

    # ------------------------------------------------------------ helpers

    def _region_from_endpoint(self) -> str:
        # ``endpoint_for_region("westus2")`` returns
        # ``https://management.westus2.azuredevcompute.io`` — recover the region
        # for serialization so resumes can rebuild the endpoint on a different host.
        endpoint = getattr(self._group_client, "_endpoint", "") or getattr(
            self._group_client, "endpoint", ""
        )
        endpoint = str(endpoint)
        if "management." in endpoint:
            try:
                return endpoint.split("management.", 1)[1].split(".", 1)[0]
            except IndexError:
                return ""
        return ""


__all__: tuple[str, ...] = ("ACASandboxesClient", "ACASandboxesSession")
