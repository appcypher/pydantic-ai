"""Local microsandbox workspace; file tools use Workspace's shell fallback."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cached_property
from uuid import uuid4

import anyio

try:
    from microsandbox import ExecEventType, ExecHandle, Network, Sandbox, default_backend_kind
    from microsandbox.errors import MicrosandboxError, SandboxNotFoundError, SandboxNotRunningError
except ImportError as error:
    raise ImportError('Install microsandbox to use the Microsandbox workspace provider') from error

from pydantic_ai.workspaces import (
    CommandResult,
    SupportsCommands,
    WorkspaceBackend,
    WorkspaceCommand,
    WorkspaceError,
    WorkspaceOutputLimitError,
    WorkspaceRef,
    WorkspaceUnavailableError,
)
from pydantic_ai_harness._workspace_provider import (
    check_env,
    check_integer,
    check_provider,
    check_timeout,
    check_working_dir,
    command_argv,
    command_deadline,
    require_asyncio,
    run_to_completion,
    stop_shielded,
)

_NAME = re.compile(r'pydantic-msb-[0-9a-f]{32}')
_OUTPUT_LIMIT = 10 * 1024 * 1024


def check_ref(ref: WorkspaceRef) -> None:
    """Keep references from history within this integration's namespace."""
    check_provider(ref, 'microsandbox')
    if _NAME.fullmatch(ref.id) is None:
        raise ValueError('Expected a workspace reference created by Microsandbox')


def check_local() -> None:
    """Refuse ambient cloud configuration instead of mutating global SDK state."""
    require_asyncio('Microsandbox requires asyncio')
    if default_backend_kind() != 'local':
        raise ValueError('This Microsandbox prototype requires the local SDK backend')


class MicrosandboxBackend(WorkspaceBackend, SupportsCommands):
    """Lazy persistent local VM with explicit cleanup and bounded command output."""

    def __init__(
        self,
        *,
        ref: WorkspaceRef | None = None,
        image: str = 'python:3.12-slim',
        working_dir: str = '/workspace',
        env: Mapping[str, str] | None = None,
        memory_mb: int = 1024,
        cpus: int = 2,
    ) -> None:
        if ref is not None:
            check_ref(ref)
        check_working_dir(working_dir)
        check_env(env)
        check_integer('memory_mb', memory_mb)
        check_integer('cpus', cpus)
        self._ref = ref
        self._image = image
        self._working_dir = working_dir
        self._env = dict(env or {})
        self._memory_mb = memory_mb
        self._cpus = cpus
        self._sandbox: Sandbox | None = None

    @cached_property
    def _lock(self) -> anyio.Lock:
        return anyio.Lock()

    @property
    def ref(self) -> WorkspaceRef | None:
        return self._ref

    async def get_sandbox(self) -> Sandbox:
        """Acquire one VM; cancellation waits until its recoverable ref is recorded."""
        check_local()
        async with self._lock:
            if self._sandbox is None:
                await run_to_completion(self._acquire)
            assert self._sandbox is not None
            return self._sandbox

    async def _acquire(self) -> None:
        if self._ref is None:
            name = f'pydantic-msb-{uuid4().hex}'
            sandbox = await Sandbox.create(
                name,
                image=self._image,
                memory=self._memory_mb,
                cpus=self._cpus,
                network=Network.none(),
                detached=True,
                ephemeral=False,
            )
            # Publish the reference before setup, so a setup failure remains recoverable.
            self._ref = WorkspaceRef(provider='microsandbox', id=name)
        else:
            try:
                handle = await Sandbox.get(self._ref.id)
                sandbox = await handle.connect_or_start(detached=True)
            except SandboxNotFoundError as error:
                raise WorkspaceUnavailableError('The microsandbox workspace no longer exists') from error
        setup = await sandbox.exec('mkdir', ['-p', '--', self._working_dir], timeout=30)
        if setup.exit_code != 0:
            raise WorkspaceError('Could not prepare the microsandbox working directory')
        canonical = await sandbox.exec('pwd', ['-P'], cwd=self._working_dir, timeout=30)
        if canonical.exit_code != 0:
            raise WorkspaceError('Could not resolve the microsandbox working directory')
        self._working_dir = canonical.stdout_text.rstrip('\n')
        self._sandbox = sandbox

    async def working_dir(self) -> str:
        await self.get_sandbox()
        return self._working_dir

    async def run(
        self,
        command: WorkspaceCommand,
        *,
        shell: bool = False,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        argv = command_argv(command, shell)
        check_env(env)
        check_timeout(timeout)
        sandbox = await self.get_sandbox()
        stdout = bytearray()
        stderr = bytearray()

        def output() -> tuple[str, str]:
            return stdout.decode('utf-8', errors='replace'), stderr.decode('utf-8', errors='replace')

        # The shell supplies conventional 126/127 exits and closes stdin. Arguments remain argv,
        # never interpolated into the script. Shield acquisition so a late stream can be killed.
        opened: ExecHandle | None = None

        async def open_stream() -> ExecHandle:
            nonlocal opened
            opened = await sandbox.exec_stream(
                '/bin/sh',
                ['-c', 'exec "$@" </dev/null', 'microsandbox', *argv],
                cwd=self._working_dir,
                env={**self._env, **(env or {})},
            )
            return opened

        try:
            stream = await run_to_completion(open_stream)
        except BaseException:
            if opened is not None:
                await stop_shielded(opened.kill)
            raise
        try:
            async with command_deadline(timeout, stop=stream.kill, output=output):
                while (event := await stream.recv()) is not None:
                    if event.event_type in (ExecEventType.STDOUT, ExecEventType.STDERR):
                        data = event.data or b''
                        if len(stdout) + len(stderr) + len(data) > _OUTPUT_LIMIT:
                            out, err = output()
                            raise WorkspaceOutputLimitError(
                                'Command output exceeded 10 MiB', limit=_OUTPUT_LIMIT, stdout=out, stderr=err
                            )
                        target = stdout if event.event_type == ExecEventType.STDOUT else stderr
                        target.extend(data)
                    elif event.event_type == ExecEventType.EXITED:
                        if event.code is None:
                            raise WorkspaceError('microsandbox returned an exit event without a code')
                        out, err = output()
                        return CommandResult(exit_code=event.code, stdout=out, stderr=err)
                    elif event.event_type == ExecEventType.FAILED:
                        raise WorkspaceError('microsandbox could not start the command')
                # Destroying a VM closes its stream without an exit event. Check liveness only
                # on this abnormal path so ordinary commands incur no extra control-plane call.
                try:
                    with anyio.fail_after(5):
                        await sandbox.ping()
                except (MicrosandboxError, TimeoutError) as error:
                    raise WorkspaceUnavailableError('The microsandbox workspace is unavailable') from error
                raise WorkspaceError('microsandbox command ended without an exit status')
        except (SandboxNotFoundError, SandboxNotRunningError) as error:
            raise WorkspaceUnavailableError('The microsandbox workspace is unavailable') from error


async def destroy(ref: WorkspaceRef) -> None:
    """Delete only an explicitly selected provider workspace, without starting it."""
    check_ref(ref)
    check_local()

    async def remove() -> None:
        try:
            handle = await Sandbox.get(ref.id)
        except SandboxNotFoundError:
            return
        await handle.destroy(timeout=30)

    await run_to_completion(remove)
