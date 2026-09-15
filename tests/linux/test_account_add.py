import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
from test_bulk import auth


class AddTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('account_add'), 'Account import/OAuth module missing')
        import account_add
        return account_add

    def test_parse_codex_auth_validates_entire_batch_and_hides_secrets(self):
        mod = self.module()
        self.assertEqual(len(mod.parse_auth_json('\ufeff'+json.dumps([auth(), auth('second@example.com')]))), 2)
        for invalid in [{'OPENAI_API_KEY': 'secret-never-show'}, {'tokens': []}, [auth(), {'tokens': {}}], '{secret-never-show']:
            with self.assertRaises(ValueError) as raised:
                mod.parse_auth_json(invalid if isinstance(invalid, str) else json.dumps(invalid))
            self.assertNotIn('secret-never-show', str(raised.exception))

    def test_import_preserves_duplicate_and_assigns_only_selected_pool(self):
        mod = self.module()
        import pools
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'default/sessions').mkdir(parents=True)
            store = hub.Store(base / 'data')
            try:
                store.settings['default_home'] = str(base / 'default')
                added, skipped, warning = mod.import_accounts(store, [auth(), auth()], [1], 'a note')
                self.assertEqual(len(added), 1)
                self.assertEqual(len(skipped), 1)
                self.assertFalse(warning)
                config = pools.load_config(store.root)
                self.assertEqual(config['pools'][0]['members'], [])
                self.assertEqual(config['pools'][1]['members'], [added[0]['id']])
                third = pools.add_pool(config)
                pools.save_config(store.root, config)
                extra, _, _ = mod.import_accounts(store, [auth('pool3@example.com')], [2], '')
                self.assertEqual(pools.load_config(store.root)['pools'][2]['members'], [extra[0]['id']])
                self.assertEqual(third['id'], 'pool-3')
                self.assertEqual(added[0]['name'], 'person@example.com')
                self.assertEqual(added[0]['note'], 'a note')
                saved = hub.read_file(store.profile(added[0]) / 'auth.json')
                mod.import_accounts(store, [auth()], [], '')
                self.assertEqual(hub.read_file(store.profile(added[0]) / 'auth.json'), saved)
                original_import = store.import_auth
                def fail_second(document, email):
                    if email == 'fail@example.com':
                        raise OSError('synthetic disk failure')
                    return original_import(document, email)
                with patch.object(store, 'import_auth', side_effect=fail_second):
                    saved_accounts, _, warning = mod.import_accounts(store, [auth('saved@example.com'), auth('fail@example.com')], [0], '')
                self.assertEqual(len(saved_accounts), 1)
                self.assertIn('stopped', warning)
                self.assertIn(saved_accounts[0]['id'], pools.load_config(store.root)['pools'][0]['members'])
            finally:
                store.close()

    def test_oauth_callback_is_state_bound_single_use_and_cancellable(self):
        mod = self.module()
        flow = mod.OAuthFlow()
        state = parse_qs(urlsplit(flow.url).query)['state'][0]
        query = parse_qs(urlsplit(flow.url).query)
        self.assertEqual(query['originator'], ['codex_vscode'])
        self.assertIn('api.connectors.invoke', query['scope'][0].split())
        with self.assertRaises(ValueError):
            flow.submit('http://localhost:1455/auth/callback?code=synthetic&state=wrong')
        flow.submit('http://localhost:1455/auth/callback?code=synthetic&state='+state)
        with self.assertRaises(ValueError):
            flow.submit('http://localhost:1455/auth/callback?code=synthetic&state='+state)
        tokens = auth()['tokens']
        with patch('bulk.exchange_code', return_value=tokens):
            result = flow.finish()
        self.assertEqual(hub.auth_email(result), 'person@example.com')
        flow = mod.OAuthFlow()
        flow.close()
        with self.assertRaises(ValueError):
            flow.finish()

    def test_real_loopback_callback_validates_state_and_closes_listener(self):
        flow = self.module().OAuthFlow()
        self.assertTrue(flow.listen(port=0))
        port = flow.server.server_port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(state):
            return urllib.request.Request(f'http://127.0.0.1:{port}/auth/callback?code=synthetic&state={state}', headers={'Host': 'localhost:1455'})
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(request('wrong'), timeout=2)
            self.assertEqual(error.exception.code, 400)
            with opener.open(request(flow.state), timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertNotIn(b'synthetic', response.read())
            with self.assertRaises(urllib.error.HTTPError):
                opener.open(request(flow.state), timeout=2)
        finally:
            flow.close()
        self.assertIsNone(flow.server)


if __name__ == '__main__':
    unittest.main()
