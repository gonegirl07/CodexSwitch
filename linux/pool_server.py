#!/usr/bin/env python3
"""Authenticated localhost Codex API pools. Run separately from the Tk UI."""
import argparse
import asyncio
from collections import OrderedDict
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, WSServerHandshakeError, WSMsgType, web

import hub
import pools
from pools import PoolError

MAX_BODY = 64 * 1024 * 1024
UPSTREAM = 'https://chatgpt.com/backend-api'
TOKEN_URL = 'https://auth.openai.com/oauth/token'


def error_body(error):
    return {'error': {'type': 'pool_error', 'code': error.code, 'message': str(error)}}


async def read_limited(response):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > MAX_BODY:
            raise PoolError(502, 'upstream_too_large', 'Upstream response exceeded 64 MiB.')
        chunks.append(chunk)
    return b''.join(chunks)


async def sse_events(response):
    """Yield untouched SSE frames plus decoded data for completion/account bookkeeping."""
    buffer = b''
    async for chunk in response.content.iter_any():
        buffer += chunk
        if len(buffer) > MAX_BODY:
            raise PoolError(502, 'event_too_large', 'Upstream event exceeded 64 MiB.')
        while True:
            lf, crlf = buffer.find(b'\n\n'), buffer.find(b'\r\n\r\n')
            if lf < 0 and crlf < 0:
                break
            end, length = (crlf, 4) if crlf >= 0 and (lf < 0 or crlf < lf) else (lf, 2)
            frame, buffer = buffer[:end+length], buffer[end+length:]
            data = b'\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b'data:'))
            try:
                value = json.loads(data)
            except ValueError:
                value = None
            yield frame, value if isinstance(value, dict) else None
    if buffer.strip():
        raise PoolError(502, 'incomplete_stream', 'Upstream ended in the middle of an SSE event.')


class PoolService:
    def __init__(self, root, upstream=UPSTREAM, token_url=TOKEN_URL):
        self.root = hub.ordinary(root)
        self.upstream, self.token_url = upstream.rstrip('/'), token_url
        self.config = pools.load_config(self.root)
        self.usage, self.cooldowns, self.errors, self.current = {}, {}, {}, {}
        self.account_locks, self.pool_locks = {}, []
        self.references = OrderedDict()
        self.inflight = {}
        self.inflight_by_pool = {}
        self.ensure_pool_runtime()
        self.request_diagnostics = []
        self.image_diagnostics = []
        self.client = None
        self.tasks = set()
        self.started_at = time.time()
        self.listeners, self.network_errors = [], []

    def ensure_pool_runtime(self):
        n = len(self.config['pools'])
        while len(self.pool_locks) < n:
            self.pool_locks.append(asyncio.Lock())
        for index in range(n):
            self.inflight_by_pool.setdefault(str(index), {})

    def account_priorities(self):
        document = json.loads(hub.read_file(self.root / 'accounts.json'))
        return {account['id']: hub.account_priority_value(account)
                for account in document.get('accounts', []) if isinstance(account, dict) and account.get('id')}

    async def start(self):
        self.client = ClientSession(timeout=ClientTimeout(total=None, sock_connect=20, sock_read=300), trust_env=True)

    async def close(self):
        if self.client:
            await self.client.close()

    def account(self, identity):
        document = json.loads(hub.read_file(self.root / 'accounts.json'))
        if document.get('version') != 1:
            raise PoolError(503, 'accounts_invalid', 'Unsupported account file.')
        if identity not in {a.get('id') for a in document.get('accounts', [])}:
            raise PoolError(503, 'account_removed', 'Pool member no longer exists.')
        return hub.ordinary(self.root / 'profiles' / identity)

    async def credential(self, identity, force=False):
        lock = self.account_locks.setdefault(identity, asyncio.Lock())
        async with lock:
            path = self.account(identity) / 'auth.json'
            original = hub.read_file(path)
            auth = json.loads(original)
            tokens = auth.get('tokens') or {}
            if not tokens.get('access_token') or not tokens.get('account_id'):
                raise PoolError(401, 'login_required', 'Pool member needs a ChatGPT login.')
            expiry = hub.jwt_payload(tokens['access_token']).get('exp')
            if force or (type(expiry) in (int, float) and expiry <= time.time() + 120):
                if not tokens.get('refresh_token'):
                    raise PoolError(401, 'login_required', 'Pool member needs a new login.')
                async with self.client.post(self.token_url, data={
                    'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann', 'grant_type': 'refresh_token',
                    'refresh_token': tokens['refresh_token']}, timeout=ClientTimeout(total=30), allow_redirects=False) as response:
                    if response.status != 200:
                        raise PoolError(401, 'login_required', 'OAuth refresh failed. Log in again in Hub.')
                    renewed = json.loads(await read_limited(response))
                if not isinstance(renewed.get('access_token'), str) or not renewed['access_token']:
                    raise PoolError(401, 'login_required', 'OAuth refresh returned no access token.')
                for key in ('access_token', 'refresh_token', 'id_token'):
                    if renewed.get(key):
                        tokens[key] = renewed[key]
                auth['last_refresh'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                if hub.read_file(path) == original:
                    hub.atomic_write(path, json.dumps(auth).encode())
                else:
                    tokens = json.loads(hub.read_file(path)).get('tokens') or {}
            return {'Authorization': 'Bearer ' + tokens['access_token'],
                    'ChatGPT-Account-Id': tokens['account_id']}

    async def headers(self, identity, incoming=None):
        headers = {'User-Agent': 'codex_cli_rs/0.153.4', 'originator': 'codex_cli_rs',
                   'Accept': 'text/event-stream', 'Content-Type': 'application/json',
                   'OpenAI-Beta': 'responses=experimental'}
        for key, value in (incoming or {}).items():
            lower = key.lower()
            if lower in ('user-agent', 'originator', 'openai-beta', 'session_id', 'conversation_id', 'version') or lower.startswith('x-codex-') or lower.startswith('x-openai-'):
                headers[key] = value
        headers.update(await self.credential(identity))
        return headers

    async def image_headers(self, identity, incoming=None):
        headers = await self.headers(identity, incoming)
        headers['Accept'] = 'application/json'
        headers['User-Agent'] = 'codex_cli_rs/0.153.4'
        headers['originator'] = 'codex_cli_rs'
        headers.pop('OpenAI-Beta', None)
        return headers

    async def refresh_usage(self, identity):
        headers = await self.headers(identity)
        for attempt in range(2):
            async with self.client.get(self.upstream + '/wham/usage', headers=headers, allow_redirects=False, timeout=ClientTimeout(total=20)) as response:
                if response.status == 401 and attempt == 0:
                    headers.update(await self.credential(identity, force=True))
                    continue
                if response.status != 200:
                    raise PoolError(503, 'quota_unavailable', 'Cannot verify account quota right now.')
                data = json.loads(await read_limited(response))
                quota = pools.normalize_usage(data)
                if not quota['windows']:
                    raise PoolError(503, 'quota_unknown', 'Account did not report supported quota windows.')
                self.usage[identity] = quota
                self.errors.pop(identity, None)
                return

    async def select(self, index, excluded=None, pinned=None):
        self.config = pools.load_config(self.root)
        self.ensure_pool_runtime()
        async with self.pool_locks[index]:
            self.config = pools.load_config(self.root)
            self.ensure_pool_runtime()
            policy = self.config['pools'][index]
            members = pools.ranked_members(policy, self.account_priorities(), self.current.get(index))
            if pinned:
                members = [pinned] if pinned in members else []
            for identity in members:
                if identity in (excluded or set()) or self.cooldowns.get(identity, 0) > time.time():
                    continue
                try:
                    if not pools.quota_fresh(self.usage.get(identity)):
                        await self.refresh_usage(identity)
                    if pools.eligible(self.usage.get(identity), policy):
                        self.current[index] = identity
                        self.write_status()
                        return identity
                    self.errors[identity] = 'At quota threshold, or required window unavailable.'
                except (PoolError, ClientError, OSError, ValueError, KeyError, asyncio.TimeoutError):
                    self.errors[identity] = 'Quota/authentication check failed; retry after 15 seconds.'
                    self.cooldowns[identity] = time.time() + 15
            self.write_status()
            if pinned:
                raise PoolError(409, 'previous_response_not_found', 'The previous account cannot continue. Resend full input without previous_response_id to allow rotation.')
            raise PoolError(429, 'pool_exhausted', 'No pool member has verified quota above both configured thresholds. Check Hub or wait for reset.')

    def write_status(self):
        hub.atomic_write(self.root / 'pool-status.json', json.dumps({
            'pid': os.getpid(), 'started_at': self.started_at, 'updated_at': time.time(),
            'current': {str(k): v for k, v in self.current.items()}, 'usage': self.usage,
            'errors': self.errors, 'inflight': self.inflight,
            'inflight_by_pool': self.inflight_by_pool,
            'listeners': self.listeners, 'network_errors': self.network_errors,
            'last_request_diagnostics': self.request_diagnostics[-1] if self.request_diagnostics else None,
            'last_image_diagnostics': self.image_diagnostics[-1] if self.image_diagnostics else None,
        }).encode())

    def record_request_diagnostics(self, index, path, body):
        tools = body.get('tools') if isinstance(body.get('tools'), list) else []
        diagnostics = {
            'endpoint': path,
            'pool': index + 1,
            'model': body.get('model'),
            'input_shape': 'string' if isinstance(body.get('input'), str) else 'items' if isinstance(body.get('input'), list) else type(body.get('input')).__name__,
            'tool_types': [tool.get('type') for tool in tools if isinstance(tool, dict)],
            'tool_models': [tool.get('model') for tool in tools if isinstance(tool, dict) and tool.get('model')],
            'has_image_tool': any(isinstance(tool, dict) and tool.get('type') == 'image_generation' for tool in tools),
            'has_tool_choice': 'tool_choice' in body,
            'stream': bool(body.get('stream')),
        }
        self.request_diagnostics.append(diagnostics)
        del self.request_diagnostics[:-50]
        print('request_diagnostics ' + json.dumps(diagnostics, ensure_ascii=False), flush=True)
        self.write_status()

    def begin_image_diagnostics(self, index, path, body, adapter):
        tools = body.get('tools') if isinstance(body.get('tools'), list) else []
        has_image_tool = any(isinstance(tool, dict) and tool.get('type') == 'image_generation' for tool in tools)
        model = getattr(adapter, 'image_model', None) or body.get('model')
        if not has_image_tool and not (isinstance(model, str) and model.startswith('gpt-image-')):
            return None
        return {
            'endpoint': path,
            'pool': index + 1,
            'request_model': model,
            'upstream_model': body.get('model'),
            'input_shape': 'string' if isinstance(body.get('input'), str) else 'items' if isinstance(body.get('input'), list) else type(body.get('input')).__name__,
            'tool_types': [tool.get('type') for tool in tools if isinstance(tool, dict)],
            'tool_models': [tool.get('model') for tool in tools if isinstance(tool, dict) and tool.get('model')],
            'has_tool_choice': 'tool_choice' in body,
            'upstream_status': None,
            'response_event_types': [],
            'response_output_types': [],
            'has_image_generation_call': False,
            'has_image_data': False,
        }

    def finish_image_diagnostics(self, diagnostics, completed=None, status=None):
        if not diagnostics:
            return
        if status is not None:
            diagnostics['upstream_status'] = status
        if isinstance(completed, dict):
            output = completed.get('output') or []
            data = completed.get('data') if isinstance(completed.get('data'), list) else []
            diagnostics['response_output_types'] = [item.get('type') for item in output if isinstance(item, dict)]
            diagnostics['has_image_generation_call'] = any(item.get('type') == 'image_generation_call' for item in output if isinstance(item, dict))
            diagnostics['has_image_data'] = any(
                item.get('type') == 'image_generation_call' and isinstance(item.get('result'), str) and item.get('result')
                for item in output if isinstance(item, dict)
            ) or any(isinstance(item, dict) and isinstance(item.get('b64_json'), str) and item['b64_json'] for item in data)
        self.image_diagnostics.append(diagnostics)
        del self.image_diagnostics[:-20]
        print('image_diagnostics ' + json.dumps(diagnostics, ensure_ascii=False), flush=True)
        self.write_status()

    def track_request(self, index, identity, delta):
        self.inflight[identity] = max(0, self.inflight.get(identity, 0) + delta)
        counts = self.inflight_by_pool[str(index)]
        count = counts.get(identity, 0) + delta
        if count > 0:
            counts[identity] = count
        else:
            counts.pop(identity, None)
        self.write_status()

    def mark_limited(self, identity, headers=None):
        try:
            delay = max(1, min(3600, float((headers or {}).get('Retry-After', 60))))
        except ValueError:
            delay = 60
        self.cooldowns[identity] = time.time() + delay
        self.usage.pop(identity, None)
        self.errors[identity] = 'Upstream rate limit; account cooling down.'
        self.write_status()

    def record_event(self, index, identity, event):
        if not isinstance(event, dict):
            return
        response = event.get('response') or {}
        if isinstance(response, dict) and isinstance(response.get('id'), str):
            self.references[(index, response['id'])] = identity
            while len(self.references) > 10000:
                self.references.popitem(last=False)
        if event.get('type') in ('response.failed', 'error'):
            error = response.get('error') or event.get('error') or {}
            code = str(error.get('code', '')) if isinstance(error, dict) else ''
            if any(word in code for word in ('rate_limit', 'usage_limit', 'quota')):
                self.mark_limited(identity)
        if event.get('type') in ('response.completed', 'response.done'):
            # Recheck live usage before the next request, including other pool's use.
            self.usage.pop(identity, None)

    def pinned(self, index, body):
        reference = body.get('previous_response_id')
        if not reference:
            return None
        identity = self.references.get((index, reference))
        if not identity:
            raise PoolError(409, 'previous_response_not_found', 'Unknown previous response. Resend full input without previous_response_id.')
        return identity

    def application(self, index):
        @web.middleware
        async def protect(request, handler):
            try:
                config = pools.load_config(self.root)
                key = config['pools'][index]['key']
                provided = request.headers.get('Authorization', '')
                if not secrets.compare_digest(provided, 'Bearer ' + key):
                    raise PoolError(401, 'invalid_api_key', 'Use the API key for this pool.')
                return await handler(request)
            except PoolError as error:
                return web.json_response(error_body(error), status=error.status,
                                         headers={'Retry-After': '15'} if error.status == 429 else None)
            except web.HTTPException:
                raise
            except (ClientError, asyncio.TimeoutError):
                return web.json_response(error_body(PoolError(502, 'upstream_unavailable', 'Upstream connection failed or timed out.')), status=502)
            except (ValueError, KeyError, TypeError, OSError):
                return web.json_response(error_body(PoolError(400, 'invalid_request', 'Invalid request or unavailable local account configuration.')), status=400)
        app = web.Application(middlewares=[protect], client_max_size=MAX_BODY)
        async def dispatch(request):
            return await self.handle(index, request)
        app.router.add_route('*', '/{tail:.*}', dispatch)
        return app

    async def handle(self, index, request):
        path = request.path
        for prefix in ('/backend-api/codex', '/codex', '/v1'):
            if path.startswith(prefix + '/'):
                path = path[len(prefix):]
                break
        if path == '/health' and request.method == 'GET':
            return web.json_response({'status': 'ok', 'pool': f'pool-{index+1}', 'pid': os.getpid()})
        if path == '/models' and request.method == 'GET':
            identity = await self.select(index)
            async with self.client.get(self.upstream + '/codex/models', params={'client_version': request.query.get('client_version', '0.153.4')}, headers=await self.headers(identity, request.headers), allow_redirects=False) as upstream:
                if upstream.status != 200:
                    raise PoolError(upstream.status, 'models_unavailable', 'Account model catalog could not be loaded.')
                data = json.loads(await read_limited(upstream))
            policy = pools.load_config(self.root)['pools'][index]
            if policy.get('image_model'):
                from pool_compat import IMAGE_COMPAT_MODELS
                existing = {m.get('slug') or m.get('id') for m in data.get('models', []) if isinstance(m, dict)}
                extras = [{'slug': model, 'display_name': model, 'input_modalities': ['text', 'image'], 'output_modalities': ['image'],
                           'experimental_supported_tools': ['image_generation']}
                          for model in IMAGE_COMPAT_MODELS if model not in existing]
                data['models'] = (data.get('models') or []) + extras
            for model in data.get('models') or []:
                if isinstance(model, dict) and not (isinstance(model.get('display_name'), str) and model['display_name'].strip()):
                    model['display_name'] = str(model.get('slug') or model.get('id') or 'model')
            data['object'] = 'list'
            data['data'] = [{'id': m.get('slug') or m.get('id'), 'object': 'model', 'created': 0, 'owned_by': 'openai'} for m in data.get('models', [])]
            return web.json_response(data)
        if path in ('/responses', '/responses/lite') and request.method == 'GET' and request.headers.get('Upgrade', '').lower() == 'websocket':
            return await self.websocket(index, request, path)
        if request.method != 'POST' or path not in ('/responses', '/responses/lite', '/responses/compact', '/chat/completions', '/images/generations'):
            raise PoolError(404, 'unsupported_endpoint', 'Supported: models, responses, responses/lite, responses/compact, chat/completions, images/generations.')
        body = await request.json()
        if not isinstance(body, dict):
            raise PoolError(400, 'invalid_request', 'Request body must be a JSON object.')
        adapter = None
        if path == '/chat/completions':
            from pool_compat import ChatAdapter
            adapter = ChatAdapter(body)
            body, path = adapter.body, '/responses'
        elif path == '/images/generations':
            from pool_compat import ImageAdapter
            return await self.images(index, request, ImageAdapter(body))
        return await self.responses(index, request, path, body, adapter)

    async def images(self, index, request, adapter):
        with ExitStack() as activity:
            return await self._images(index, request, adapter, activity)

    async def _images(self, index, request, adapter, activity):
        body = dict(adapter.body)
        image_diagnostics = self.begin_image_diagnostics(index, '/images/generations', body, adapter)
        self.record_request_diagnostics(index, '/images/generations', body)
        excluded = set()
        while True:
            identity = await self.select(index, excluded)
            activity.callback(self.track_request, index, identity, -1)
            self.track_request(index, identity, 1)
            headers = await self.image_headers(identity, request.headers)
            upstream = await self.client.post(self.upstream + '/codex/images/generations', json=body,
                                              headers=headers, allow_redirects=False)
            if upstream.status == 401:
                upstream.release()
                await self.credential(identity, force=True)
                headers = await self.image_headers(identity, request.headers)
                upstream = await self.client.post(self.upstream + '/codex/images/generations', json=body,
                                                  headers=headers, allow_redirects=False)
            if upstream.status == 429:
                self.mark_limited(identity, upstream.headers)
                upstream.release()
                excluded.add(identity)
                activity.close()
                continue
            raw = await read_limited(upstream)
            status = upstream.status
            upstream.release()
            if status >= 400:
                self.finish_image_diagnostics(image_diagnostics, status=status)
                try:
                    error = json.loads(raw)
                except ValueError:
                    error = error_body(PoolError(status, 'upstream_error', f'Upstream rejected request (HTTP {status}).'))
                return web.json_response(error, status=status)
            try:
                completed = json.loads(raw)
            except ValueError:
                self.finish_image_diagnostics(image_diagnostics, status=status)
                raise PoolError(502, 'invalid_image_response', 'Upstream image response was not JSON.')
            if not isinstance(completed, dict):
                self.finish_image_diagnostics(image_diagnostics, status=status)
                raise PoolError(502, 'invalid_image_response', 'Upstream image response was not a JSON object.')
            try:
                result = adapter.result(completed)
            finally:
                self.finish_image_diagnostics(image_diagnostics, completed, status)
            return web.json_response(result)

    async def responses(self, index, request, path, body, adapter=None):
        # Keep activity across the entire upstream attempt, including header wait.
        with ExitStack() as activity:
            return await self._responses(index, request, path, body, adapter, activity)

    async def _responses(self, index, request, path, body, adapter, activity):
        stream = body.get('stream', False) if adapter is None else adapter.stream
        if not isinstance(body.get('model'), str) or not body['model']:
            raise PoolError(400, 'model_required', 'Choose a model returned by /v1/models.')
        if body.get('background'):
            raise PoolError(400, 'background_unsupported', 'Background Responses are not supported by this subscription pool.')
        body = dict(body)
        body.setdefault('instructions', 'You are a helpful assistant.')
        if isinstance(body.get('input'), str):
            body['input'] = [{'role': 'user', 'content': body['input']}]
        compact = path == '/responses/compact'
        ignored = []
        image_diagnostics = self.begin_image_diagnostics(index, path, body, adapter)
        if not compact:
            body.update(store=False, stream=True)
            # ChatGPT's Codex endpoint rejects this Platform-only option. Generic
            # Responses SDKs (including pi) supply it automatically. Report the
            # normalization explicitly; there is no server-enforced output cap.
            if 'max_output_tokens' in body:
                body.pop('max_output_tokens')
                ignored.append('max_output_tokens')
        self.record_request_diagnostics(index, path, body)
        pinned, excluded = self.pinned(index, body), set()
        while True:
            identity = await self.select(index, excluded, pinned)
            activity.callback(self.track_request, index, identity, -1)
            self.track_request(index, identity, 1)
            upstream = await self.client.post(self.upstream + '/codex' + path, json=body,
                                              headers=await self.headers(identity, request.headers), allow_redirects=False)
            if upstream.status == 401:
                upstream.release()
                await self.credential(identity, force=True)
                upstream = await self.client.post(self.upstream + '/codex' + path, json=body,
                                                  headers=await self.headers(identity, request.headers), allow_redirects=False)
            if upstream.status == 429:
                self.mark_limited(identity, upstream.headers)
                upstream.release()
                excluded.add(identity)
                activity.close()
                continue
            if upstream.status >= 400:
                status = upstream.status
                raw = await read_limited(upstream)
                upstream.release()
                self.finish_image_diagnostics(image_diagnostics, status=status)
                # Preserve structured service errors, never attach credentials or local paths.
                try:
                    error = json.loads(raw)
                except ValueError:
                    error = error_body(PoolError(status, 'upstream_error', f'Upstream rejected request (HTTP {status}).'))
                return web.json_response(error, status=status)
            break
        downstream = None
        try:
            # Native Responses requests above always ask for SSE. Some gateways
            # omit Content-Type entirely; only an explicit JSON type overrides it.
            if compact or 'application/json' in upstream.headers.get('Content-Type', '').lower():
                data = json.loads(await read_limited(upstream))
                self.finish_image_diagnostics(image_diagnostics, data, upstream.status)
                return web.json_response(data, status=upstream.status)
            completed, output_items, output_size = None, {}, 0
            if stream:
                response_headers = {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
                if ignored:
                    response_headers['X-Hub-Ignored-Parameters'] = ','.join(ignored)
                downstream = web.StreamResponse(headers=response_headers)
                await downstream.prepare(request)
            async for frame, event in sse_events(upstream):
                self.record_event(index, identity, event)
                if image_diagnostics and event and event.get('type'):
                    image_diagnostics['response_event_types'].append(event['type'])
                if event and event.get('type') == 'response.output_item.done' and (not stream or adapter):
                    item = event.get('item')
                    if isinstance(item, dict):
                        output_size += len(json.dumps(item))
                        if output_size > MAX_BODY:
                            raise PoolError(502, 'output_too_large', 'Accumulated response exceeded 64 MiB; use native streaming.')
                        output_items[event['output_index']] = item
                if event and event.get('type') in ('response.completed', 'response.done', 'response.incomplete'):
                    completed = event.get('response')
                    if isinstance(completed, dict) and not completed.get('output') and output_items:
                        completed = dict(completed, output=[output_items[i] for i in sorted(output_items)])
                        event = dict(event, response=completed)
                if event and event.get('type') in ('response.failed', 'error') and not stream:
                    raise PoolError(502, 'response_failed', 'Upstream response failed; request was not replayed.')
                if downstream:
                    if adapter:
                        for output in adapter.event(event):
                            await downstream.write(output)
                    else:
                        await downstream.write(frame)
            if not completed:
                raise PoolError(502, 'incomplete_stream', 'Upstream ended without a completed response; request was not replayed.')
            if downstream:
                self.finish_image_diagnostics(image_diagnostics, completed, upstream.status)
                if adapter:
                    await downstream.write(b'data: [DONE]\n\n')
                await downstream.write_eof()
                return downstream
            try:
                result = adapter.result(completed) if adapter else completed
            finally:
                self.finish_image_diagnostics(image_diagnostics, completed, upstream.status)
            return web.json_response(result, headers={'X-Hub-Ignored-Parameters': ','.join(ignored)} if ignored else None)
        except (PoolError, ClientError, asyncio.TimeoutError) as error:
            if not downstream:
                raise
            message = error if isinstance(error, PoolError) else PoolError(502, 'upstream_interrupted', 'Upstream stream interrupted; request was not replayed.')
            event = {'type': 'error', **error_body(message)}
            await downstream.write(('data: ' + json.dumps(event) + '\n\n').encode())
            await downstream.write_eof()
            return downstream
        finally:
            upstream.close()

    async def websocket(self, index, request, path):
        downstream = web.WebSocketResponse(max_msg_size=MAX_BODY, heartbeat=30)
        await downstream.prepare(request)
        upstream, identity = None, None
        try:
            async for message in downstream:
                if message.type != WSMsgType.TEXT:
                    if message.type == WSMsgType.ERROR:
                        break
                    continue
                body = json.loads(message.data)
                if not isinstance(body, dict) or body.get('type') != 'response.create':
                    raise PoolError(400, 'invalid_request', 'Expected response.create.')
                pinned = self.pinned(index, body)
                excluded = set()
                while True:
                    selected = await self.select(index, excluded, pinned=pinned)
                    if identity != selected or upstream is None or upstream.closed:
                        if upstream:
                            await upstream.close()
                        identity = selected
                        headers = await self.headers(identity, request.headers)
                        headers['OpenAI-Beta'] = request.headers.get('OpenAI-Beta', 'responses_websockets=2026-02-06')
                        try:
                            upstream = await self.client.ws_connect(self.upstream + '/codex' + path, headers=headers, max_msg_size=MAX_BODY, heartbeat=30)
                        except WSServerHandshakeError as error:
                            if error.status == 429:
                                self.mark_limited(identity, error.headers)
                                excluded.add(identity)
                                continue
                            raise
                    break
                body = dict(body, store=False)
                body.setdefault('instructions', 'You are a helpful assistant.')
                await upstream.send_json(body)
                self.track_request(index, identity, 1)
                try:
                    output_started = False
                    while True:
                        next_upstream = asyncio.create_task(upstream.receive(timeout=300))
                        next_client = asyncio.create_task(downstream.receive())
                        try:
                            done, _ = await asyncio.wait({next_upstream, next_client}, return_when=asyncio.FIRST_COMPLETED)
                            if next_client in done:
                                client_message = next_client.result()
                                if client_message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                                    return downstream
                                raise PoolError(409, 'response_in_progress', 'Wait for the current response before sending another response.create.')
                            event_message = next_upstream.result()
                        finally:
                            for task in (next_upstream, next_client):
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(next_upstream, next_client, return_exceptions=True)
                        if event_message.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                            raise PoolError(502, 'upstream_interrupted', 'Upstream WebSocket closed; request was not replayed.')
                        if event_message.type == WSMsgType.BINARY:
                            output_started = True
                            await downstream.send_bytes(event_message.data)
                            continue
                        event = json.loads(event_message.data)
                        self.record_event(index, identity, event)
                        error = event.get('error') or {}
                        if (not output_started and body.get('previous_response_id')
                                and event.get('type') == 'error' and event.get('status') == 400
                                and isinstance(error, dict) and not error.get('code')
                                and error.get('type') == 'invalid_request_error'
                                and error.get('message') == 'Invalid `previous_response_id`.'):
                            # Codex retries the full logical request for this code.
                            # Never strip the reference: input may only be a delta.
                            event['error'] = dict(error, code='previous_response_not_found',
                                                  param='previous_response_id')
                            await downstream.send_json(event)
                            return downstream
                        await downstream.send_str(event_message.data)
                        if event.get('type') in ('response.completed', 'response.done', 'response.failed', 'response.incomplete', 'error'):
                            break
                        if event.get('type') not in ('response.created', 'response.in_progress', 'response.queued'):
                            output_started = True
                finally:
                    self.track_request(index, identity, -1)
        except (PoolError, ClientError, OSError, ValueError, asyncio.TimeoutError) as error:
            problem = error if isinstance(error, PoolError) else PoolError(502, 'websocket_failed', 'WebSocket failed; reconnect with full input to retry.')
            if not downstream.closed:
                await downstream.send_json({'type': 'error', **error_body(problem)})
        finally:
            if upstream:
                await upstream.close()
            await downstream.close()
        return downstream


async def sync_listeners(runners, sites, policies, networks):
    desired = {(i, host, policy['port']) for i, policy in enumerate(policies) for host in pools.bind_hosts(policy, networks)}
    # Remove old interface addresses first. Never fall back to 0.0.0.0.
    for key in set(sites) - desired:
        await sites.pop(key).stop()
    errors = []
    for key in sorted(desired - set(sites)):
        index, host, port = key
        site = web.TCPSite(runners[index], host, port)
        try:
            await site.start()
            sites[key] = site
        except OSError:
            if site in runners[index].sites:
                await site.stop()
            errors.append(f'Pool {index+1}: cannot listen on {host}:{port}. Check address/port availability.')
    return errors


async def serve(root):
    service = PoolService(root)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    runners, sites = [], {}
    await service.start()
    try:
        for index, pool in enumerate(service.config['pools']):
            runner = web.AppRunner(service.application(index), access_log=None, shutdown_timeout=10)
            runners.append(runner)
            await runner.setup()
        while not stop.is_set():
            try:
                networks = await asyncio.to_thread(pools.network_interfaces)
            except (OSError, ValueError, subprocess.SubprocessError):
                networks = []
            service.config = pools.load_config(root)
            service.ensure_pool_runtime()
            while len(runners) < len(service.config['pools']):
                index = len(runners)
                runner = web.AppRunner(service.application(index), access_log=None, shutdown_timeout=10)
                runners.append(runner)
                await runner.setup()
            service.network_errors = await sync_listeners(runners, sites, service.config['pools'], networks)
            service.listeners = [{'pool': i, 'host': host, 'port': port} for i, host, port in sites]
            service.write_status()
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
    finally:
        for runner in runners:
            await runner.cleanup()
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'codex-cli-hub')
    args = parser.parse_args()
    os.umask(0o077)
    root = hub.ordinary(args.data_dir)
    lock_path = hub.ordinary(root / 'pool-server.lock')
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Pool server already running for this data directory.', flush=True)
            return 1
        try:
            asyncio.run(serve(root))
        except (OSError, ValueError) as error:
            print(f'Pool startup failed ({type(error).__name__}); check ports and configuration.', flush=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
