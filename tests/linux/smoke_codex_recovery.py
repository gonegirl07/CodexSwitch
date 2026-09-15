"""Installed Codex recovery through a real pool and a local fake upstream.

No real credentials, requests, or client configuration are used.
Run: python3 tests/linux/smoke_codex_recovery.py
"""
import asyncio
import json
import os
import signal

from aiohttp import web
from test_pool_server import ServerTests
import hub


class RecoveryProbe(ServerTests):
    async def upstream(self, request):
        if request.headers.get('Upgrade', '').lower() != 'websocket':
            return await super().upstream(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            body = json.loads(message.data)
            self.calls.append((request.headers.get('ChatGPT-Account-Id'), body))
            if body.get('previous_response_id') and not self.rejected:
                self.rejected = True
                await ws.send_json({'type': 'error', 'status': 400, 'error': {
                    'type': 'invalid_request_error', 'message': 'Invalid `previous_response_id`.'}})
                continue
            result = {'id': 'ws-one', 'status': 'completed', 'output': []}
            if body.get('generate') is not False:
                result['output'] = [{'id': 'msg-one', 'type': 'message', 'role': 'assistant',
                                     'status': 'completed', 'content': [{'type': 'output_text', 'text': 'OK', 'annotations': []}]}]
                await ws.send_json({'type': 'response.output_text.delta', 'delta': 'OK',
                                    'item_id': 'msg-one', 'output_index': 0, 'content_index': 0})
                await ws.send_json({'type': 'response.output_item.done', 'output_index': 0,
                                    'item': result['output'][0]})
            await ws.send_json({'type': 'response.completed', 'response': result})
        return ws

    async def probe(self):
        self.rejected = False
        temporary = str(self.root / 'client')
        os.mkdir(temporary)
        env = hub.isolated_env(temporary)
        env['CODEX_POOL_KEY'] = self.config['pools'][0]['key']
        command = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '-C', temporary,
                   '-m', 'native-test', '-c', 'model_provider="hub"',
                   '-c', 'model_providers.hub.name="Hub"',
                   '-c', f'model_providers.hub.base_url="{self.servers[0].make_url("/v1")}"',
                   '-c', 'model_providers.hub.env_key="CODEX_POOL_KEY"',
                   '-c', 'model_providers.hub.wire_api="responses"',
                   '-c', 'model_providers.hub.supports_websockets=true',
                   'Remember pineapple. Reply exactly OK. Do not use tools.']
        process = await asyncio.create_subprocess_exec(*command, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        assert process.returncode == 0, f'Codex exit {process.returncode}: {stderr.decode()[-1000:]}'
        assert b'OK' in stdout, 'Missing final response'
        assert self.rejected, 'Probe did not exercise previous_response_id'
        rejected_index = next(i for i, (_, b) in enumerate(self.calls) if b.get('previous_response_id'))
        recovered = [b for _, b in self.calls[rejected_index + 1:] if not b.get('previous_response_id')]
        assert recovered, 'Codex did not resend full input'
        assert any('pineapple' in json.dumps(b.get('input')) for b in recovered), 'Lost original context'
        assert len([b for b in recovered if b.get('generate') is not False]) == 1, 'Duplicate recovery generation'
        print('PASS: installed Codex recovered automatically with original context after invalid previous_response_id.')


async def main():
    probe = RecoveryProbe()
    await probe.asyncSetUp()
    try:
        await probe.probe()
    finally:
        await probe.asyncTearDown()


if __name__ == '__main__':
    asyncio.run(main())
