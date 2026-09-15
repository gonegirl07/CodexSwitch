"""Opt-in live, tiny Codex/pi probes through existing local pools.

No default client configs are edited; the only writes are temporary CLI state.
Run: python3 tests/linux/smoke_pool_clients.py
"""
import asyncio
import json
import os
import signal
from pathlib import Path
import sys
import tempfile
from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import pools


async def main():
    root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'codex-cli-hub'
    config = pools.load_config(root)
    pool = config['pools'][0]
    async with ClientSession() as client:
        async with client.get(f'http://127.0.0.1:{pool["port"]}/v1/models', headers={'Authorization': 'Bearer '+pool['key']}) as response:
            response.raise_for_status()
            available = [m['id'] for m in (await response.json())['data']]
    model = os.environ.get('HUB_TEST_MODEL') or next((m for m in ('gpt-5.4-mini', 'gpt-5.6-luna', 'gpt-5.6-sol') if m in available), None)
    assert model in available, 'Set HUB_TEST_MODEL to a model returned by /v1/models'
    for websocket in (False, True):
        pool = config['pools'][0]
        with tempfile.TemporaryDirectory(prefix='pool-codex-probe-') as temporary:
            env = hub.isolated_env(temporary)
            env['CODEX_POOL_KEY'] = pool['key']
            command = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '-C', temporary,
                       '-m', model, '-c', 'model_provider="hub"',
                       '-c', 'model_providers.hub.name="Hub"',
                       '-c', f'model_providers.hub.base_url="http://127.0.0.1:{pool["port"]}/v1"',
                       '-c', 'model_providers.hub.env_key="CODEX_POOL_KEY"',
                       '-c', 'model_providers.hub.wire_api="responses"',
                       '-c', 'model_providers.hub.supports_websockets=' + str(websocket).lower(),
                       'Reply exactly OK. Do not use any tools.']
            process = await asyncio.create_subprocess_exec(*command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                raise AssertionError('Codex probe timed out') from None
            finally:
                # CLI plugin helpers can outlive the CLI and write into CODEX_HOME.
                # Stop only this probe's dedicated process group before cleanup.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            # Do not print full logs: client diagnostics may include request details.
            assert process.returncode == 0, f'Codex failed (exit {process.returncode}); output: {stdout.decode()[:80]}'
            assert 'OK' in stdout.decode(), 'Codex did not return expected output'
            print('Installed Codex client passed:', 'WebSocket enabled' if websocket else 'SSE', flush=True)
    module = Path.home() / '.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-responses.js'
    if module.exists():
        pool = config['pools'][1]
        env = dict(os.environ, HUB_URL=f'http://127.0.0.1:{pool["port"]}/v1', HUB_KEY=pool['key'], PI_MODULE=module.as_uri(), HUB_MODEL=model)
        script = '''
const {stream} = await import(process.env.PI_MODULE);
const model = {id:process.env.HUB_MODEL, provider:"hub", api:"openai-responses", baseUrl:process.env.HUB_URL,
reasoning:true,input:["text","image"],contextWindow:272000,maxTokens:32,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
const result = await stream(model,{systemPrompt:"Reply exactly OK.",messages:[{role:"user",content:"Reply OK.",timestamp:Date.now()}]},
{apiKey:process.env.HUB_KEY,maxTokens:32}).result();
if(result.stopReason === "error") throw new Error(result.errorMessage);
if(!result.content.some(c=>c.type==="text" && c.text.includes("OK"))) throw new Error("Missing text output");
console.log("Installed pi Responses transport passed through Pool 2.");
'''
        process = await asyncio.create_subprocess_exec('node', '--input-type=module', '-e', script, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise AssertionError('pi probe timed out') from None
        if process.returncode:
            print('pi error:', stderr.decode()[:1500])
        assert process.returncode == 0, 'pi transport failed'
        print(stdout.decode().strip())


if __name__ == '__main__':
    asyncio.run(main())
