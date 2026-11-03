"""Provide a local microsandbox workspace to Harness tools."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.workspaces import WorkspaceBackend, WorkspaceRef
from pydantic_ai_harness._workspace_provider import check_env, check_integer, check_working_dir, reject_defer_loading

from ._backend import MicrosandboxBackend, check_ref, destroy


@dataclass(kw_only=True)
class Microsandbox(AbstractCapability[AgentDepsT]):
    """Run tools in a persistent local microVM with guest networking disabled."""

    image: str = 'python:3.12-slim'
    working_dir: str = '/workspace'
    memory_mb: int = 1024
    cpus: int = 2
    env: Mapping[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        reject_defer_loading('Microsandbox', self.defer_loading)
        check_working_dir(self.working_dir)
        check_env(self.env)
        check_integer('memory_mb', self.memory_mb)
        check_integer('cpus', self.cpus)

    def backend(self, ref: WorkspaceRef) -> MicrosandboxBackend:
        """Construct a lazy backend for a previously created workspace."""
        check_ref(ref)
        return MicrosandboxBackend(ref=ref, working_dir=self.working_dir, env=self.env)

    async def destroy(self, ref: WorkspaceRef) -> None:
        """Explicitly delete the workspace and its saved files."""
        await destroy(ref)

    def get_workspace(self, ctx: RunContext[AgentDepsT], *, ref: WorkspaceRef | None) -> WorkspaceBackend | None:
        """Select the workspace without performing I/O in the capability hook."""
        if ref is not None and ref.provider != 'microsandbox':
            return None
        return MicrosandboxBackend(
            ref=ref,
            image=self.image,
            working_dir=self.working_dir,
            env=self.env,
            memory_mb=self.memory_mb,
            cpus=self.cpus,
        )
