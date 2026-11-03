"""Live model proof: generate plots, reattach by reference, verify retained data."""

import asyncio
import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pydantic_ai.workspaces import Workspace
from pydantic_ai_harness.coder import Coder
from pydantic_ai_harness.microsandbox import Microsandbox

OUTPUT = Path('microsandbox-plotting-output')


async def main() -> None:
    capability = Microsandbox()
    agent = Agent(
        'openai:gpt-4.1',
        capabilities=[capability, Coder(sub_agents=False, repo_context=False)],
        instructions=(
            'Complete the requested work using the workspace tools. Python standard library only; '
            'no network or package installation. Write actual SVG files using Python. '
            'Use clear labels, a light background, and a readable chart. '
            'You must write AND execute your script with tools, then verify the files. '
            'Do not put Python code in your final answer. Keep your reply brief.'
        ),
    )
    ref = None
    OUTPUT.mkdir(exist_ok=True)
    try:
        print('Running OpenAI: generate hourly data + SVG heatmap...', flush=True)
        first = await agent.run(
            'Generate realistic synthetic hourly household electricity usage for all 31 days of January 2026. '
            'Save exactly 744 data rows in usage.csv with timestamp and kwh columns. '
            'Make a heatmap of usage by day and hour in heatmap.svg with a color legend. '
            'Use Python random seed 42. Save the generating script too. Check the output files exist.',
            usage_limits=UsageLimits(request_limit=12),
        )
        assert first.workspace is not None and first.workspace.ref is not None
        ref = first.workspace.ref
        print(first.output, flush=True)
        workspace = Workspace(capability.backend(ref))
        csv_before = await workspace.read_bytes('/workspace/usage.csv')
        assert len(list(csv.DictReader(io.StringIO(csv_before.decode())))) == 744
        heatmap = await workspace.read_bytes('/workspace/heatmap.svg')
        assert ET.fromstring(heatmap).tag.endswith('svg')
        (OUTPUT / 'usage.csv').write_bytes(csv_before)
        (OUTPUT / 'heatmap.svg').write_bytes(heatmap)
        print('PASS first run: 744 hourly data rows + valid heatmap.svg', flush=True)
        print(
            'Running OpenAI: reattach by workspace ref + comparison chart...',
            flush=True,
        )
        second = await agent.run(
            'Using the existing usage.csv, create comparison.svg comparing average hourly electricity usage '
            'on weekdays versus weekends. Show both series with labeled axes and a legend. '
            'Keep usage.csv and heatmap.svg unchanged. Check that comparison.svg exists.',
            workspace=ref,
            usage_limits=UsageLimits(request_limit=12),
        )
        assert second.workspace is not None and second.workspace.ref == ref
        print(second.output, flush=True)
        assert await workspace.read_bytes('/workspace/usage.csv') == csv_before
        assert await workspace.read_bytes('/workspace/heatmap.svg') == heatmap
        comparison = await workspace.read_bytes('/workspace/comparison.svg')
        assert ET.fromstring(comparison).tag.endswith('svg')
        OUTPUT.mkdir(exist_ok=True)
        for name, data in [
            ('usage.csv', csv_before),
            ('heatmap.svg', heatmap),
            ('comparison.svg', comparison),
        ]:
            (OUTPUT / name).write_bytes(data)
        summary = {
            'model': 'gpt-4.1',
            'data_rows': 744,
            'same_workspace_ref': True,
            'csv_unchanged': True,
            'heatmap_unchanged': True,
            'csv_sha256': hashlib.sha256(csv_before).hexdigest(),
            'first_usage': repr(first.usage),
            'second_usage': repr(second.usage),
            'first_output': first.output,
            'second_output': second.output,
        }
        (OUTPUT / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(
            'PASS follow-up: same VM reference, unchanged data, valid comparison.svg',
            flush=True,
        )
        print(f'Artifacts: {OUTPUT}', flush=True)
    finally:
        if ref is not None:
            await capability.destroy(ref)
            print('Cleaned up plotting VM', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
