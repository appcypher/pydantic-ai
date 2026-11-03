"""Opt-in conformance checks against real local VMs; no model credentials required."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING

import pytest

from pydantic_ai.workspaces import WorkspaceBackend, WorkspaceRef
from pydantic_ai.workspaces.conformance import WorkspaceBackendSuite

if TYPE_CHECKING:
    from pydantic_ai_harness.microsandbox import MicrosandboxBackend

pytestmark = pytest.mark.skipif(
    os.environ.get('PYDANTIC_MICROSANDBOX_LIVE') != '1', reason='Set PYDANTIC_MICROSANDBOX_LIVE=1 to boot local VMs'
)


@pytest.fixture(scope='module')
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture(scope='module')
async def make_backend() -> AsyncIterator[Callable[[], 'MicrosandboxBackend']]:
    from pydantic_ai_harness.microsandbox import Microsandbox, MicrosandboxBackend

    created: list[MicrosandboxBackend] = []

    def make() -> MicrosandboxBackend:
        backend = MicrosandboxBackend()
        created.append(backend)
        return backend

    try:
        yield make
    finally:
        for backend in created:
            if backend.ref is not None:
                await Microsandbox().destroy(backend.ref)


class TestMicrosandbox(WorkspaceBackendSuite):
    @pytest.fixture(scope='module')
    @classmethod
    def backend(cls, make_backend: Callable[[], 'MicrosandboxBackend']) -> WorkspaceBackend:
        return make_backend()

    @pytest.fixture
    def fresh_backend(self, make_backend: Callable[[], 'MicrosandboxBackend']) -> Callable[[], WorkspaceBackend]:
        return make_backend

    @pytest.fixture
    def attach_backend(self) -> Callable[[WorkspaceRef], WorkspaceBackend]:
        from pydantic_ai_harness.microsandbox import Microsandbox

        return Microsandbox().backend

    @pytest.fixture
    def destroy_environment(self) -> Callable[[WorkspaceBackend], Awaitable[None]]:
        from pydantic_ai_harness.microsandbox import Microsandbox

        async def destroy(backend: WorkspaceBackend) -> None:
            assert backend.ref is not None
            await Microsandbox().destroy(backend.ref)

        return destroy

    @pytest.fixture
    def has_real_posix_shell(self) -> bool:
        return True

    @pytest.fixture
    def can_detect_exit_with_inherited_output_pipes(self) -> bool:
        # SDK 0.7.13 waits for output EOF when a background child inherits its parent's pipes.
        return False
