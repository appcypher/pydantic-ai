# Microsandbox prototype

Run Harness commands and file tools in a persistent local [microsandbox](https://github.com/superradcompany/microsandbox) microVM. The first operation creates the VM; later runs can reattach using `result.workspace.ref`. Guest networking is disabled. File tools use Pydantic AI's shell fallback.

This is an unpublished prototype. The added `microsandbox` extra installs SDK 0.7.13, which bundles the local runtime. Use the source checkout for the provider and install `microsandbox`, `pydantic-ai-harness[coder]`, and your model's SDK.

```python
import asyncio

from pydantic_ai import Agent
from pydantic_ai_harness.coder import Coder
from pydantic_ai_harness.microsandbox import Microsandbox


async def main():
    sandbox = Microsandbox(image='python:3.12-slim')
    agent = Agent(
        'openai:gpt-4.1',
        capabilities=[sandbox, Coder(sub_agents=False, repo_context=False)],
        instructions=(
            'Use Python standard library only. Write and execute your scripts with tools, '
            'then verify the files. Do not return code in place of executing it.'
        ),
    )
    result = await agent.run(
        'Generate hourly household electricity usage for January 2026 in usage.csv. '
        'Create heatmap.svg showing usage by day and hour, with labels and a color legend.'
    )
    assert result.workspace is not None and result.workspace.ref is not None
    ref = result.workspace.ref
    try:
        await agent.run(
            'Using usage.csv, create comparison.svg comparing average hourly usage on weekdays '
            'and weekends. Keep the original data and heatmap unchanged.',
            workspace=ref,
        )
        # Read or download outputs through result.workspace before deleting the VM.
    finally:
        await sandbox.destroy(ref)


asyncio.run(main())
```

## Verification and limits

Tested on macOS Apple silicon with microsandbox 0.7.13 and Pydantic AI 2.65.0. A live GPT-4.1 run generated 744 CSV rows and two valid SVG plots across separate runs sharing one workspace reference; the second run left the original CSV and heatmap byte-for-byte unchanged. The OpenAI credential stayed on the host.

The live workspace conformance suite passes 35 checks and skips three: permission denial while running as guest root, a native-realpath-only check, and direct process-exit detection when background descendants retain output pipes. That last case is an observed SDK 0.7.13 limitation. Ordinary commands, cancellation, timeouts, filesystem operations, reattachment, and deletion are covered.

This prototype is asyncio-only and local-only. Native filesystem methods, cloud configuration, a configurable network policy, offline mock coverage, and cross-platform verification remain follow-up work. VMs persist until explicitly destroyed; callers should retain workspace references for cleanup after failed runs as well as successful ones.
