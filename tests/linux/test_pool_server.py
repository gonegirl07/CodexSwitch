import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from aiohttp import ClientSession, ClientConnectorError, web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import pools
import pool_server
from pool_server import PoolService
from test_bulk import auth


class DeployedSourceTests(unittest.TestCase):
    def test_installed_pool_sources_match_repository_when_installed(self):
        repo = Path(__file__).resolve().parents[2]
        installed = next((path for path in (
            Path.home() / '.local/share/codexswitch',
            Path.home() / '.local/share/codex-cli-hub-app',
        ) if path.exists()), None)
        if installed is None:
            self.skipTest('CodexSwitch is not installed in this account')
        for relative in ('linux/pool_compat.py', 'linux/pool_server.py', 'linux/pools.py'):
            with self.subTest(relative=relative):
                self.assertEqual((repo / relative).read_bytes(), (installed / relative).read_bytes())


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ids = ['a' * 32, 'b' * 32]
        self.calls, self.paths, self.upstream_headers, self.remaining, self.reject, self.fail_stream = [], [], [], {self.ids[0]: 80, self.ids[1]: 70}, set(), False
        self.empty_completed = False
        self.image_completed = False
        self.single_window_seconds = {}
        self.missing_content_type = False
        self.release_response = None
        self.release_headers = None
        self.ws_error = None
        self.ws_partial = False
        accounts = []
        for index, identity in enumerate(self.ids):
            profile = self.root / 'profiles' / identity
            profile.mkdir(parents=True)
            credential = auth(identity + '@example.com')
            credential['tokens']['account_id'] = identity
            hub.atomic_write(profile / 'auth.json', json.dumps(credential).encode())
            accounts.append({'id': identity, 'name': identity + '@example.com', 'priority': index})
        hub.atomic_write(self.root / 'accounts.json', json.dumps({'version': 1, 'accounts': accounts}).encode())
        self.config = pools.load_config(self.root)
        self.config['pools'][0]['members'] = self.ids[:]
        self.config['pools'][1]['members'] = [self.ids[1]]
        pools.save_config(self.root, self.config)
        upstream = web.Application()
        upstream.router.add_route('*', '/{tail:.*}', self.upstream)
        self.up = TestServer(upstream)
        await self.up.start_server()
        self.service = PoolService(self.root, upstream=str(self.up.make_url('/')).rstrip('/'))
        await self.service.start()
        self.servers = [TestServer(self.service.application(i)) for i in (0, 1)]
        for server in self.servers:
            await server.start_server()
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        for server in self.servers:
            await server.close()
        await self.service.close()
        await self.up.close()
        self.tmp.cleanup()

    def headers(self, index=0):
        return {'Authorization': 'Bearer ' + self.config['pools'][index]['key']}

    async def upstream(self, request):
        identity = request.headers.get('ChatGPT-Account-Id')
        self.assertIn(identity, self.ids)
        self.assertNotIn(request.headers['Authorization'].removeprefix('Bearer '), [p['key'] for p in self.config['pools']])
        if request.path == '/wham/usage':
            if identity in self.single_window_seconds:
                return web.json_response({'rate_limit': {'primary_window': {
                    'used_percent': 100-self.remaining[identity], 'limit_window_seconds': self.single_window_seconds[identity],
                    'reset_at': time.time()+3600}, 'secondary_window': None}})
            return web.json_response({'rate_limit': {'primary_window': {'used_percent': 100-self.remaining[identity], 'limit_window_seconds': 18000, 'reset_at': time.time()+3600}, 'secondary_window': {'used_percent': 20, 'limit_window_seconds': 604800, 'reset_at': time.time()+7200}}})
        if request.path == '/codex/models':
            return web.json_response({'models': [{'slug': 'native-test', 'input_modalities': ['text', 'image'], 'context_window': 100000}]})
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            previous = None
            async for message in ws:
                body = json.loads(message.data)
                self.calls.append((identity, body))
                error = self.ws_error
                if body.get('previous_response_id') and body['previous_response_id'] != previous:
                    error = {'type': 'error', 'status': 400, 'error': {
                        'type': 'invalid_request_error', 'message': 'Invalid `previous_response_id`.'}}
                if error:
                    if self.ws_partial:
                        await ws.send_json({'type': 'response.output_text.delta', 'delta': 'partial'})
                    await ws.send_json(error)
                    continue
                if self.release_response:
                    await self.release_response.wait()
                await ws.send_json({'type': 'response.completed', 'response': {'id': 'ws-one', 'status': 'completed', 'output': []}})
                previous = 'ws-one'
            return ws
        body = await request.json()
        self.calls.append((identity, body))
        self.paths.append(request.path)
        if self.release_headers:
            await self.release_headers.wait()
        if identity in self.reject:
            return web.json_response({'error': {'code': 'usage_limit_reached', 'message': 'quota'}}, status=429)
        if request.path == '/codex/responses/compact':
            return web.json_response({'id': 'compact-one', 'output': [{'type': 'compaction', 'encrypted_content': 'keep'}]})
        if request.path == '/codex/images/generations':
            body = await request.json()
            self.calls.append((identity, body))
            self.paths.append(request.path)
            self.upstream_headers.append({k: request.headers.get(k) for k in ('User-Agent', 'originator', 'Accept', 'OpenAI-Beta')})
            if identity in self.reject:
                return web.json_response({'error': {'code': 'usage_limit_reached', 'message': 'quota'}}, status=429)
            if self.image_completed:
                return web.json_response({'created': 1, 'data': [{'b64_json': 'YWJj'}], 'output_format': 'png'})
            return web.json_response({'created': 1, 'data': []})
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        if self.missing_content_type:
            # Some native Codex gateways omit Content-Type on SSE success.
            response.headers['Content-Type'] = ''
        await response.prepare(request)
        if self.release_response:
            await self.release_response.wait()
        if self.fail_stream:
            await response.write(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n')
            await response.write(b'data: {"type":"response.failed","response":{"error":{"code":"usage_limit_reached"}}}\n\n')
        else:
            output = [{'type': 'image_generation_call', 'result': 'YWJj'}] if self.image_completed else [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'OK'}]}]
            result = {'id': 'response-'+identity, 'object': 'response', 'status': 'completed', 'model': body['model'], 'output': output, 'usage': {'input_tokens': 2, 'output_tokens': 1, 'total_tokens': 3}}
            if self.empty_completed:
                await response.write(('data: '+json.dumps({'type': 'response.output_item.done', 'output_index': 0, 'item': result['output'][0]})+'\n\n').encode())
                result['output'] = []
            for event in [{'type': 'response.output_text.delta', 'delta': 'OK'}, {'type': 'response.completed', 'response': result}]:
                await response.write(('data: '+json.dumps(event)+'\n\n').encode())
        await response.write_eof()
        return response

    async def post(self, index=0, **body):
        return await self.client.post(self.servers[index].make_url('/v1/responses'), headers=self.headers(index), json={'model': 'native-test', 'input': 'hi', **body})

    async def test_single_window_accounts_route_and_rotate_in_both_pools(self):
        self.single_window_seconds = {self.ids[0]: 2592000, self.ids[1]: 604800}
        for index, expected in [(0, self.ids[0]), (1, self.ids[1])]:
            response = await self.post(index)
            self.assertEqual(response.status, 200)
            await response.read()
            self.assertEqual(self.calls[-1][0], expected)
        self.remaining[self.ids[0]] = 5
        self.service.usage.clear()
        response = await self.post(0)
        self.assertEqual(response.status, 200)
        await response.read()
        self.assertEqual(self.calls[-1][0], self.ids[1])

    async def test_activity_separates_pools_and_clears_completed_requests(self):
        self.release_response = asyncio.Event()
        responses = []
        try:
            responses = await asyncio.gather(self.post(0, stream=True), self.post(1, stream=True), self.post(0, stream=True))
            status = json.loads((self.root/'pool-status.json').read_bytes())
            self.assertEqual(status.get('inflight_by_pool'), {'0': {self.ids[0]: 2}, '1': {self.ids[1]: 1}})
        finally:
            self.release_response.set()
            for response in responses:
                await response.read()
        status = json.loads((self.root/'pool-status.json').read_bytes())
        self.assertEqual(status['inflight_by_pool'], {'0': {}, '1': {}})

    async def test_activity_includes_waiting_for_upstream_headers(self):
        self.release_headers = asyncio.Event()
        task = asyncio.create_task(self.post())
        try:
            for _ in range(100):
                if self.calls:
                    break
                await asyncio.sleep(.01)
            status = json.loads((self.root/'pool-status.json').read_bytes())
            self.assertEqual(status['inflight_by_pool']['0'], {self.ids[0]: 1})
        finally:
            self.release_headers.set()
            response = await task
            await response.read()
        status = json.loads((self.root/'pool-status.json').read_bytes())
        self.assertEqual(status['inflight_by_pool']['0'], {})

    async def test_websocket_activity_clears_on_completion(self):
        self.release_response = asyncio.Event()
        ws = await self.client.ws_connect(self.servers[1].make_url('/v1/responses'), headers=self.headers(1))
        try:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': 'hi'})
            for _ in range(100):
                if self.calls:
                    break
                await asyncio.sleep(.01)
            status = json.loads((self.root/'pool-status.json').read_bytes())
            self.assertEqual(status.get('inflight_by_pool', {}).get('1'), {self.ids[1]: 1})
            self.release_response.set()
            await ws.receive_json(timeout=3)
            status = json.loads((self.root/'pool-status.json').read_bytes())
            self.assertEqual(status['inflight_by_pool']['1'], {})
        finally:
            self.release_response.set()
            await ws.close()

    async def test_native_sse_without_content_type(self):
        self.missing_content_type = True
        response = await self.post()
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['output'][0]['content'][0]['text'], 'OK')

    async def test_interface_listener_removed_when_network_disappears(self):
        self.assertTrue(hasattr(pool_server, 'sync_listeners'), 'Dynamic listeners not implemented')
        runner = web.AppRunner(self.service.application(0))
        await runner.setup()
        sites = {}
        policy = {'port': 0, 'interface': 'wifi0'}
        try:
            await pool_server.sync_listeners([runner], sites, [policy], [{'interface': 'wifi0', 'addresses': ['127.0.0.2']}])
            self.assertEqual({key[1] for key in sites}, {'127.0.0.1', '127.0.0.2'})
            site = sites[(0, '127.0.0.2', 0)]
            port = site._server.sockets[0].getsockname()[1]
            async with self.client.get(f'http://127.0.0.2:{port}/health', headers=self.headers()) as response:
                self.assertEqual(response.status, 200)
            await pool_server.sync_listeners([runner], sites, [policy], [])
            self.assertEqual({key[1] for key in sites}, {'127.0.0.1'})
            with self.assertRaises(ClientConnectorError):
                async with ClientSession() as fresh:
                    await fresh.get(f'http://127.0.0.2:{port}/health')
        finally:
            await runner.cleanup()

    async def test_image_upstream_keeps_codex_client_identity(self):
        response = await self.client.post(self.servers[0].make_url('/v1/images/generations'),
                                          headers={**self.headers(), 'User-Agent': 'Python-urllib/3.10', 'originator': 'python'},
                                          json={'model': 'gpt-image-1', 'prompt': 'A square'})
        await response.read()
        headers = self.upstream_headers[-1]
        self.assertEqual(headers['User-Agent'], 'codex_cli_rs/0.153.4')
        self.assertEqual(headers['originator'], 'codex_cli_rs')
        self.assertEqual(headers['Accept'], 'application/json')
        self.assertIsNone(headers['OpenAI-Beta'])

    async def test_image_api_forwards_client_model_to_codex_images(self):
        response = await self.client.post(self.servers[0].make_url('/v1/images/generations'),
                                          headers=self.headers(), json={'model': 'gpt-image-1', 'prompt': 'A square'})
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())['error']['code'], 'image_not_generated')
        self.assertEqual(self.calls[-1][0], self.ids[0])
        self.assertEqual(self.paths[-1], '/codex/images/generations')
        self.assertEqual(self.calls[-1][1]['model'], 'gpt-image-1')
        self.assertEqual(self.calls[-1][1]['prompt'], 'A square')
        self.assertNotIn('tools', self.calls[-1][1])
        self.assertNotIn('input', self.calls[-1][1])

    async def test_pool_2_image_api_forwards_gpt_image_25_to_codex_images(self):
        response = await self.client.post(self.servers[1].make_url('/v1/images/generations'),
                                          headers=self.headers(1),
                                          json={'model': 'gpt-image-2.5-sunburst', 'prompt': 'A square', 'quality': 'low'})
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())['error']['code'], 'image_not_generated')
        self.assertEqual(self.paths[-1], '/codex/images/generations')
        self.assertEqual(self.calls[-1][0], self.ids[1])
        self.assertEqual(self.calls[-1][1]['model'], 'gpt-image-2.5-sunburst')
        self.assertEqual(self.calls[-1][1]['quality'], 'low')
        self.assertNotIn('tools', self.calls[-1][1])
        self.assertNotIn('tool_choice', self.calls[-1][1])

    async def test_image_api_converts_codex_images_data_to_b64_json(self):
        self.image_completed = True
        response = await self.client.post(self.servers[1].make_url('/v1/images/generations'),
                                          headers=self.headers(1),
                                          json={'model': 'gpt-image-2.5-flare', 'prompt': 'A square'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['data'], [{'b64_json': 'YWJj'}])
        diagnostics = self.service.image_diagnostics[-1]
        self.assertEqual(diagnostics['upstream_status'], 200)
        self.assertEqual(diagnostics['endpoint'], '/images/generations')
        self.assertTrue(diagnostics['has_image_data'])
        self.assertNotIn('Authorization', json.dumps(diagnostics))
        self.assertNotIn('A square', json.dumps(diagnostics))

    async def test_image_api_records_missing_image_diagnostics(self):
        response = await self.client.post(self.servers[1].make_url('/v1/images/generations'),
                                          headers=self.headers(1),
                                          json={'model': 'gpt-image-2.5-sunburst', 'prompt': 'A square'})
        self.assertEqual(response.status, 502)
        diagnostics = self.service.image_diagnostics[-1]
        self.assertEqual(diagnostics['request_model'], 'gpt-image-2.5-sunburst')
        self.assertEqual(diagnostics['upstream_model'], 'gpt-image-2.5-sunburst')
        self.assertEqual(diagnostics['endpoint'], '/images/generations')
        self.assertFalse(diagnostics['has_image_data'])

    async def test_native_image_tool_records_diagnostics(self):
        response = await self.client.post(self.servers[1].make_url('/backend-api/codex/responses'),
                                          headers=self.headers(1),
                                          json={'model': 'native-test', 'input': 'draw a square',
                                                'tools': [{'type': 'image_generation', 'model': 'gpt-image-2.5-sunburst'}],
                                                'tool_choice': 'auto'})
        self.assertEqual(response.status, 200)
        await response.read()
        diagnostics = self.service.image_diagnostics[-1]
        self.assertEqual(diagnostics['endpoint'], '/responses')
        self.assertEqual(diagnostics['request_model'], 'native-test')
        self.assertEqual(diagnostics['tool_types'], ['image_generation'])
        self.assertEqual(diagnostics['tool_models'], ['gpt-image-2.5-sunburst'])
        self.assertTrue(diagnostics['has_tool_choice'])
        self.assertNotIn('draw a square', json.dumps(diagnostics))

    async def test_request_summary_records_native_text_without_prompt_or_secrets(self):
        response = await self.client.post(self.servers[1].make_url('/backend-api/codex/responses'),
                                          headers={**self.headers(1), 'User-Agent': 'desktop-test'},
                                          json={'model': 'native-test', 'input': 'secret prompt', 'stream': False})
        self.assertEqual(response.status, 200)
        await response.read()
        diagnostics = self.service.request_diagnostics[-1]
        self.assertEqual(diagnostics['endpoint'], '/responses')
        self.assertEqual(diagnostics['pool'], 2)
        self.assertEqual(diagnostics['model'], 'native-test')
        self.assertEqual(diagnostics['tool_types'], [])
        self.assertFalse(diagnostics['has_image_tool'])
        self.assertFalse(diagnostics['has_tool_choice'])
        encoded = json.dumps(diagnostics)
        self.assertNotIn('secret prompt', encoded)
        self.assertNotIn(self.config['pools'][1]['key'], encoded)

    async def test_keys_isolate_pools_and_native_models(self):
        async with self.client.get(self.servers[1].make_url('/v1/models'), headers=self.headers(0)) as response:
            self.assertEqual(response.status, 401)
        async with self.client.get(self.servers[0].make_url('/v1/models'), headers=self.headers()) as response:
            data = await response.json()
            self.assertEqual(data['data'][0]['id'], 'native-test')
            self.assertEqual(data['models'][0]['context_window'], 100000)
        async with self.client.get(self.servers[0].make_url('/v1/../../auth.json'), headers=self.headers()) as response:
            self.assertEqual(response.status, 404)

    async def test_models_exposes_image_compat_models_when_bridge_configured(self):
        self.config['pools'][1]['image_model'] = 'native-test'
        pools.save_config(self.root, self.config)
        async with self.client.get(self.servers[1].make_url('/v1/models'), headers=self.headers(1)) as response:
            data = await response.json()
        ids = [model['id'] for model in data['data']]
        self.assertIn('native-test', ids)
        self.assertIn('gpt-image-1', ids)
        self.assertIn('gpt-image-2', ids)
        self.assertIn('gpt-image-2.5-sunburst', ids)
        self.assertIn('gpt-image-2.5-flare', ids)
        self.assertNotIn('gpt-image-2.5', ids)
        raw = {model.get('slug'): model for model in data['models']}
        self.assertEqual(raw['gpt-image-2.5-sunburst']['output_modalities'], ['image'])
        self.assertEqual(raw['gpt-image-2.5-sunburst']['experimental_supported_tools'], ['image_generation'])
        self.assertEqual(raw['gpt-image-2.5-sunburst']['display_name'], 'gpt-image-2.5-sunburst')
        self.assertTrue(all(isinstance(model.get('display_name'), str) and model['display_name'] for model in data['models']))

    async def test_models_hides_image_compat_models_without_bridge_model(self):
        self.config['pools'][1]['image_model'] = ''
        pools.save_config(self.root, self.config)
        async with self.client.get(self.servers[1].make_url('/v1/models'), headers=self.headers(1)) as response:
            data = await response.json()
        ids = [model['id'] for model in data['data']]
        self.assertNotIn('gpt-image-1', ids)
        self.assertNotIn('gpt-image-2.5-sunburst', ids)

    async def test_threshold_switch_and_pool_membership(self):
        async with await self.post() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['output'][0]['content'][0]['text'], 'OK')
        self.remaining[self.ids[0]] = 5
        self.service.usage.clear()
        async with await self.post() as response:
            self.assertEqual(response.status, 200)
        async with await self.post(index=1) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual([a for a, body in self.calls], [self.ids[0], self.ids[1], self.ids[1]])

    async def test_select_honors_account_priority_when_starting_fresh(self):
        document = json.loads((self.root / 'accounts.json').read_text())
        document['accounts'][0]['priority'] = 4
        document['accounts'][1]['priority'] = 0
        hub.atomic_write(self.root / 'accounts.json', json.dumps(document).encode())
        async with await self.post() as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.calls[0][0], self.ids[1])

    async def test_429_retries_before_output_and_exhaustion(self):
        self.reject.add(self.ids[0])
        async with await self.post(stream=True, tools=[{'type': 'custom', 'name': 'native-tool', 'format': {'type': 'text'}}]) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('response.completed', await response.text())
        self.assertEqual([a for a, b in self.calls], self.ids)
        self.assertEqual(self.calls[-1][1]['tools'][0]['type'], 'custom')
        self.reject.add(self.ids[1])
        async with await self.post() as response:
            self.assertEqual(response.status, 429)
        self.assertEqual(self.service.inflight_by_pool, {'0': {}, '1': {}})

    async def test_no_replay_after_stream_output(self):
        self.fail_stream = True
        async with await self.post(stream=True) as response:
            self.assertIn('partial', await response.text())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.inflight_by_pool, {'0': {}, '1': {}})

    async def test_previous_response_stays_on_account_or_requires_full_context(self):
        async with await self.post() as response:
            identity = (await response.json())['id']
        self.remaining[self.ids[0]] = 0
        self.service.usage.clear()
        async with await self.post(previous_response_id=identity) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())['error']['code'], 'previous_response_not_found')

    async def test_websocket_and_compact(self):
        async with self.client.ws_connect(self.servers[0].make_url('/v1/responses'), headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': []})
            message = await ws.receive_json(timeout=5)
            self.assertEqual(message['type'], 'response.completed')
        async with self.client.post(self.servers[0].make_url('/v1/responses/compact'), headers=self.headers(), json={'model': 'native-test', 'input': []}) as response:
            self.assertEqual((await response.json())['output'][0]['encrypted_content'], 'keep')

    async def test_websocket_reconnect_requests_client_full_context_recovery(self):
        url = self.servers[0].make_url('/v1/responses')
        full = [{'role': 'user', 'content': 'Remember pineapple.'}]
        async with self.client.ws_connect(url, headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': full})
            first = await ws.receive_json(timeout=5)
            reference = first['response']['id']
            await ws.send_json({'type': 'response.create', 'model': 'native-test',
                                'previous_response_id': reference, 'input': []})
            self.assertEqual((await ws.receive_json(timeout=5))['type'], 'response.completed')
        async with self.client.ws_connect(url, headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test',
                                'previous_response_id': reference, 'input': []})
            error = await ws.receive_json(timeout=5)
            self.assertEqual(error['error'].get('code'), 'previous_response_not_found')
            self.assertEqual(error['status'], 400)
            self.assertEqual(error['error']['param'], 'previous_response_id')
        # The client, not the proxy, owns and replays the complete history.
        async with self.client.ws_connect(url, headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': full})
            self.assertEqual((await ws.receive_json(timeout=5))['type'], 'response.completed')
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[2][1]['previous_response_id'], reference)
        self.assertEqual(self.calls[3][1]['input'], full)
        self.assertEqual(self.service.inflight_by_pool, {'0': {}, '1': {}})

    async def test_websocket_unrelated_invalid_request_is_unchanged(self):
        self.ws_error = {'type': 'error', 'status': 400, 'error': {
            'type': 'invalid_request_error', 'message': 'Invalid model.'}}
        async with self.client.ws_connect(self.servers[0].make_url('/v1/responses'), headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': []})
            self.assertEqual(await ws.receive_json(timeout=5), self.ws_error)
        self.assertEqual(len(self.calls), 1)

    async def test_websocket_invalid_reference_without_reference_is_not_retryable(self):
        self.ws_error = {'type': 'error', 'status': 400, 'error': {
            'type': 'invalid_request_error', 'message': 'Invalid `previous_response_id`.'}}
        async with self.client.ws_connect(self.servers[0].make_url('/v1/responses'), headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': []})
            self.assertEqual(await ws.receive_json(timeout=5), self.ws_error)
        self.assertEqual(len(self.calls), 1)

    async def test_websocket_invalid_reference_after_output_is_not_retryable(self):
        async with self.client.ws_connect(self.servers[0].make_url('/v1/responses'), headers=self.headers()) as ws:
            await ws.send_json({'type': 'response.create', 'model': 'native-test', 'input': []})
            reference = (await ws.receive_json(timeout=5))['response']['id']
            self.ws_error = {'type': 'error', 'status': 400, 'error': {
                'type': 'invalid_request_error', 'message': 'Invalid `previous_response_id`.'}}
            self.ws_partial = True
            await ws.send_json({'type': 'response.create', 'model': 'native-test',
                                'previous_response_id': reference, 'input': []})
            self.assertEqual((await ws.receive_json(timeout=5))['delta'], 'partial')
            self.assertEqual(await ws.receive_json(timeout=5), self.ws_error)
        self.assertEqual(len(self.calls), 2)

    async def test_nonstream_rebuilds_items_when_native_completed_output_is_empty(self):
        self.empty_completed = True
        async with await self.post(max_output_tokens=32) as response:
            self.assertEqual(response.headers['X-Hub-Ignored-Parameters'], 'max_output_tokens')
            result = await response.json()
            self.assertEqual(result['output'][0]['content'][0]['text'], 'OK')
        self.assertNotIn('max_output_tokens', self.calls[-1][1])


if __name__ == '__main__':
    unittest.main()
