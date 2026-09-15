import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
from pool_compat import ChatAdapter, ImageAdapter
from pool_server import PoolError


class CompatTests(unittest.TestCase):
    def test_chat_tools_and_images_convert_without_losing_call_ids(self):
        adapter = ChatAdapter({'model': 'native-test', 'messages': [
            {'role': 'system', 'content': 'Be useful'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'look'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,abc', 'detail': 'high'}}]},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{"path":"a"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'file content'}],
            'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}],
            'reasoning_effort': 'high', 'stream': True})
        self.assertEqual(adapter.body['instructions'], 'Be useful')
        self.assertEqual(adapter.body['input'][0]['content'][1]['type'], 'input_image')
        self.assertEqual(adapter.body['input'][1]['call_id'], 'call-1')
        self.assertEqual(adapter.body['input'][2]['type'], 'function_call_output')
        self.assertEqual(adapter.body['tools'][0]['name'], 'read')
        self.assertEqual(adapter.body['reasoning']['effort'], 'high')
        self.assertTrue(adapter.stream)

    def test_chat_sse_tool_calls_and_usage(self):
        adapter = ChatAdapter({'model': 'test', 'messages': [], 'stream': True, 'stream_options': {'include_usage': True}})
        chunks = []
        for event in [
            {'type': 'response.output_item.added', 'output_index': 1, 'item': {'type': 'function_call', 'id': 'fc1', 'call_id': 'call1', 'name': 'read', 'arguments': ''}},
            {'type': 'response.function_call_arguments.delta', 'output_index': 1, 'delta': '{}'},
            {'type': 'response.completed', 'response': {'id': 'r1', 'output': [{'type': 'function_call', 'call_id': 'call1', 'name': 'read', 'arguments': '{}'}], 'usage': {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5}}}]:
            chunks.extend(adapter.event(event))
        decoded = [json.loads(chunk.decode().removeprefix('data: ').strip()) for chunk in chunks]
        self.assertTrue(any(c['choices'] and c['choices'][0]['delta'].get('tool_calls', [{}])[0].get('id') == 'call1' for c in decoded))
        self.assertEqual(decoded[-1]['usage']['total_tokens'], 5)
        self.assertTrue(any(c['choices'] and c['choices'][0]['finish_reason'] == 'tool_calls' for c in decoded))

    def test_image_generation_extracts_base64_and_rejects_unsupported_options(self):
        adapter = ImageAdapter({'model': 'gpt-image-1', 'prompt': 'a cat', 'size': '1024x1024'})
        self.assertEqual(adapter.body, {'model': 'gpt-image-1', 'prompt': 'a cat', 'size': '1024x1024'})
        self.assertNotIn('tools', adapter.body)
        result = adapter.result({'created': 1, 'data': [{'b64_json': 'YWJj'}]})
        self.assertEqual(result['data'], [{'b64_json': 'YWJj'}])
        for request in [{'n': 2}, {'response_format': 'url'}, {'stream': True}, {'unexpected': 'ignored?'}]:
            with self.assertRaises(PoolError):
                ImageAdapter({'model': 'gpt-image-1', 'prompt': 'cat', **request})

    def test_image_generation_forwards_official_gpt_image_25_model(self):
        for model in (
            'gpt-image-2.5-sunburst',
            'gpt-image-2.5-sunburst-2026-09-08',
            'gpt-image-2.5-flare',
            'gpt-image-2.5-flare-2026-09-08',
        ):
            with self.subTest(model=model):
                adapter = ImageAdapter({'model': model, 'prompt': 'a cat', 'quality': 'low'})
                self.assertEqual(adapter.body['model'], model)
                self.assertEqual(adapter.body['prompt'], 'a cat')
                self.assertEqual(adapter.body['quality'], 'low')
                self.assertNotIn('tools', adapter.body)
                self.assertNotIn('input', adapter.body)
                self.assertNotIn('tool_choice', adapter.body)

        with self.assertRaisesRegex(PoolError, 'gpt-image-2.5-flare'):
            ImageAdapter({'model': 'gpt-image-2.5-sol', 'prompt': 'a cat'})
        with self.assertRaisesRegex(PoolError, 'gpt-image-2.5-flare'):
            ImageAdapter({'model': 'gpt-image-2.5', 'prompt': 'a cat'})

    def test_image_generation_accepts_codex_gpt_image_2_alias(self):
        adapter = ImageAdapter({'model': 'gpt-image-2', 'prompt': 'a cat', 'output_format': 'png'})
        self.assertEqual(adapter.body, {'model': 'gpt-image-2', 'prompt': 'a cat', 'output_format': 'png'})

    def test_image_generation_rejects_text_fallbacks_explicitly(self):
        adapter = ImageAdapter({'model': 'gpt-image-2.5-sunburst', 'prompt': 'a cat'})
        with self.assertRaisesRegex(PoolError, 'without image data'):
            adapter.result({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '<svg></svg>'}]}]})
        with self.assertRaisesRegex(PoolError, 'without image data'):
            adapter.result({'created': 1, 'data': []})


if __name__ == '__main__':
    unittest.main()
