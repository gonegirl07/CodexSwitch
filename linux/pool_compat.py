"""Explicit compatibility adapters. Native /responses requests bypass these."""
import json
import time
import uuid

from pools import PoolError

IMAGE_COMPAT_ALIAS = 'gpt-image-1'
CODEX_IMAGE_ALIAS = 'gpt-image-2'
GPT_IMAGE_25_MODELS = (
    'gpt-image-2.5-sunburst',
    'gpt-image-2.5-sunburst-2026-09-08',
    'gpt-image-2.5-flare',
    'gpt-image-2.5-flare-2026-09-08',
)
IMAGE_COMPAT_MODELS = (IMAGE_COMPAT_ALIAS, CODEX_IMAGE_ALIAS) + GPT_IMAGE_25_MODELS


def invalid(message):
    return PoolError(400, 'unsupported_parameter', message)


def usage(data):
    return {'prompt_tokens': data.get('input_tokens', 0), 'completion_tokens': data.get('output_tokens', 0),
            'total_tokens': data.get('total_tokens', 0),
            'prompt_tokens_details': {'cached_tokens': (data.get('input_tokens_details') or {}).get('cached_tokens', 0)}}


class ChatAdapter:
    def __init__(self, request):
        if not isinstance(request.get('messages'), list):
            raise invalid('messages must be an array.')
        if request.get('n', 1) != 1:
            raise invalid('Only n=1 is supported.')
        for key in ('audio', 'modalities', 'logprobs', 'top_logprobs', 'logit_bias', 'stop', 'frequency_penalty', 'presence_penalty'):
            if request.get(key) is not None:
                raise invalid(f'{key} is not supported by the Chat Completions adapter. Use native Responses where applicable.')
        self.stream = request.get('stream', False)
        self.include_usage = bool((request.get('stream_options') or {}).get('include_usage'))
        self.body = {'model': request.get('model'), 'input': [], 'instructions': '', 'stream': True, 'store': False}
        self.id = 'chatcmpl-' + uuid.uuid4().hex
        self.created = int(time.time())
        self.tool_indexes = {}
        instructions = []
        for message in request['messages']:
            role, content = message.get('role'), message.get('content')
            if role in ('system', 'developer'):
                if isinstance(content, str):
                    instructions.append(content)
                elif isinstance(content, list):
                    instructions.extend(part['text'] for part in content if part.get('type') == 'text')
            elif role == 'tool':
                self.body['input'].append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': content if isinstance(content, str) else json.dumps(content)})
            elif role in ('user', 'assistant'):
                if content:
                    if isinstance(content, str):
                        parts = [{'type': 'output_text' if role == 'assistant' else 'input_text', 'text': content}]
                    elif isinstance(content, list):
                        parts = []
                        for part in content:
                            if part.get('type') == 'text':
                                parts.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': part['text']})
                            elif part.get('type') == 'image_url' and role == 'user':
                                image = part['image_url']
                                parts.append({'type': 'input_image', 'image_url': image['url'], 'detail': image.get('detail', 'auto')})
                            else:
                                raise invalid('Unsupported chat content block. Use native Responses for richer content.')
                    else:
                        raise invalid('Message content must be text or an array.')
                    self.body['input'].append({'role': role, 'content': parts})
                for call in message.get('tool_calls') or []:
                    if call.get('type') != 'function':
                        raise invalid('Chat adapter supports function tools; native custom tools use /responses.')
                    self.body['input'].append({'type': 'function_call', 'call_id': call['id'], **call['function']})
            else:
                raise invalid('Unsupported chat message role.')
        self.body['instructions'] = '\n\n'.join(instructions) or 'You are a helpful assistant.'
        if 'tools' in request:
            tools = []
            for tool in request['tools']:
                if tool.get('type') != 'function':
                    raise invalid('Use /responses for non-function native tools.')
                tools.append({'type': 'function', **tool['function']})
            self.body['tools'] = tools
        choice = request.get('tool_choice')
        if choice is not None:
            self.body['tool_choice'] = {'type': 'function', 'name': choice['function']['name']} if isinstance(choice, dict) else choice
        for key in ('parallel_tool_calls', 'service_tier', 'temperature', 'top_p', 'prompt_cache_key'):
            if key in request:
                self.body[key] = request[key]
        if request.get('reasoning_effort'):
            self.body['reasoning'] = {'effort': request['reasoning_effort']}
        if request.get('response_format'):
            fmt = request['response_format']
            self.body['text'] = {'format': {'type': 'json_schema', **fmt['json_schema']} if fmt['type'] == 'json_schema' else fmt}
        for key in ('max_tokens', 'max_completion_tokens'):
            if request.get(key) is not None:
                self.body['max_output_tokens'] = request[key]

    def chunk(self, delta, finish=None, tokens=None):
        data = {'id': self.id, 'object': 'chat.completion.chunk', 'created': self.created,
                'model': self.body['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        if tokens is not None:
            data.update(choices=[], usage=usage(tokens))
        return ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode()

    def event(self, event):
        if not event:
            return []
        kind = event.get('type')
        if kind == 'response.output_text.delta':
            return [self.chunk({'content': event['delta']})]
        if kind == 'response.refusal.delta':
            return [self.chunk({'refusal': event['delta']})]
        if kind in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
            return [self.chunk({'reasoning_content': event['delta']})]
        if kind == 'response.output_item.added' and event.get('item', {}).get('type') == 'function_call':
            item = event['item']
            index = len(self.tool_indexes)
            self.tool_indexes[event['output_index']] = index
            return [self.chunk({'tool_calls': [{'index': index, 'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item.get('arguments', '')}}]})]
        if kind == 'response.function_call_arguments.delta':
            if event['output_index'] not in self.tool_indexes:
                raise PoolError(502, 'invalid_stream', 'Tool argument delta arrived before its tool call.')
            return [self.chunk({'tool_calls': [{'index': self.tool_indexes[event['output_index']], 'function': {'arguments': event['delta']}}]})]
        if kind in ('response.completed', 'response.done', 'response.incomplete'):
            result = self.result(event['response'])
            chunks = [self.chunk({}, result['choices'][0]['finish_reason'])]
            if self.include_usage:
                chunks.append(self.chunk({}, tokens=event['response'].get('usage') or {}))
            return chunks
        if kind in ('response.failed', 'error'):
            return [('data: ' + json.dumps({'error': {'code': 'response_failed', 'message': 'Upstream response failed; request was not replayed.'}}) + '\n\n').encode()]
        return []

    def result(self, response):
        texts, calls, reasoning = [], [], []
        for item in response.get('output') or []:
            if item.get('type') == 'message':
                texts.extend(part.get('text', part.get('refusal', '')) for part in item.get('content', []))
            elif item.get('type') == 'function_call':
                calls.append({'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}})
            elif item.get('type') == 'reasoning':
                reasoning.extend(part.get('text', '') for part in item.get('summary') or [])
        message = {'role': 'assistant', 'content': ''.join(texts) or None}
        if calls:
            message['tool_calls'] = calls
        if reasoning:
            message['reasoning_content'] = ''.join(reasoning)
        finish = 'length' if response.get('status') == 'incomplete' else 'tool_calls' if calls else 'stop'
        return {'id': self.id, 'object': 'chat.completion', 'created': self.created,
                'model': response.get('model', self.body['model']),
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': usage(response.get('usage') or {})}


class ImageAdapter:
    def __init__(self, request, response_model=''):
        allowed = {'model', 'prompt', 'n', 'size', 'quality', 'background', 'output_format', 'output_compression', 'moderation', 'response_format', 'stream'}
        if set(request) - allowed:
            raise invalid('Unsupported image parameter: ' + ', '.join(sorted(set(request) - allowed)))
        if request.get('n', 1) != 1 or request.get('response_format', 'b64_json') != 'b64_json' or request.get('stream'):
            raise invalid('Image compatibility supports n=1, b64_json and non-streaming. Use /responses for native image streaming.')
        if not isinstance(request.get('prompt'), str) or not request['prompt'].strip():
            raise invalid('An image prompt is required.')
        image_model = request.get('model', IMAGE_COMPAT_ALIAS)
        if image_model not in IMAGE_COMPAT_MODELS:
            raise invalid('Unsupported image model. Use gpt-image-1/gpt-image-2 for Codex compatibility aliases, or one of: ' + ', '.join(GPT_IMAGE_25_MODELS) + '.')
        self.image_model = image_model
        self.stream = False
        self.body = {'model': image_model, 'prompt': request['prompt']}
        for field in ('size', 'quality', 'background', 'output_format', 'output_compression', 'moderation'):
            if field in request:
                self.body[field] = request[field]

    def result(self, response):
        data = [{'b64_json': item['b64_json']} for item in response.get('data') or []
                if isinstance(item, dict) and isinstance(item.get('b64_json'), str) and item['b64_json']]
        if not data:
            raise PoolError(502, 'image_not_generated',
                            f'Upstream completed without image data for {self.image_model}. '
                            'This account may not allow Codex image generation; text or SVG output is not returned as an image.')
        return {'created': response.get('created') or int(time.time()), 'data': data}
