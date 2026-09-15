import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import bulk


def token(payload):
    return 'header.' + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=') + '.signature'


def auth(email='person@example.com'):
    return {'auth_mode': 'chatgpt', 'tokens': {
        'access_token': token({'https://api.openai.com/profile': {'email': email}, 'https://api.openai.com/auth': {'chatgpt_account_id': 'account-1'}}),
        'id_token': token({'email': email}), 'refresh_token': 'synthetic-refresh', 'account_id': 'account-1'}}


class BulkTests(unittest.TestCase):
    def test_retry_uses_fresh_oauth_and_closes_each_browser(self):
        browser = MagicMock()
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        attempts = []
        def drive(page, credentials, callback, cancel, progress):
            attempts.append(page)
            if len(attempts) < 3:
                raise bulk.RetryableLoginError('A temporary OpenAI login page error occurred.')
            return 'code'
        with patch('playwright.sync_api.sync_playwright') as factory, patch.object(bulk, 'capture_callback'), \
                patch.object(bulk, 'drive_login', side_effect=drive), \
                patch.object(bulk, 'exchange_code', return_value=auth()['tokens']), \
                patch.object(bulk, 'authorization', wraps=bulk.authorization) as authorize:
            factory.return_value.__enter__.return_value = playwright
            result = bulk.login_one(('person@example.com', 'password', ''), threading.Event(), lambda s: None)
        self.assertEqual(hub.auth_email(result), 'person@example.com')
        self.assertEqual(authorize.call_count, 3)
        self.assertEqual(browser.close.call_count, 3)
        self.assertEqual(playwright.chromium.launch.call_args.kwargs.get('channel'), 'chrome')

    def test_missing_chrome_falls_back_and_cancel_does_not_launch(self):
        with patch('playwright.sync_api.sync_playwright') as factory, patch.object(bulk, 'capture_callback'), \
                patch.object(bulk, 'drive_login', return_value='code'), \
                patch.object(bulk, 'exchange_code', return_value=auth()['tokens']):
            launch = factory.return_value.__enter__.return_value.chromium.launch
            launch.side_effect = [RuntimeError('Chrome unavailable'), MagicMock()]
            result = bulk.login_one(('person@example.com', 'password', ''), threading.Event(), lambda s: None)
            self.assertEqual(hub.auth_email(result), 'person@example.com')
            self.assertNotIn('channel', launch.call_args.kwargs)
            launch.reset_mock()
            cancel = threading.Event()
            cancel.set()
            with self.assertRaisesRegex(ValueError, 'Cancelled'):
                bulk.login_one(('person@example.com', 'password', ''), cancel, lambda s: None)
            self.assertEqual(launch.call_count, 0)

    def test_retry_stops_after_three_and_does_not_retry_rejection(self):
        for error, count in [(bulk.RetryableLoginError('temporary login error'), 3), (ValueError('Login rejected'), 1)]:
            with self.subTest(error=type(error).__name__), patch('playwright.sync_api.sync_playwright') as factory, \
                    patch.object(bulk, 'capture_callback'), patch.object(bulk, 'drive_login', side_effect=error) as drive:
                with self.assertRaisesRegex(ValueError, str(error)):
                    bulk.login_one(('person@example.com', 'password', ''), threading.Event(), lambda s: None)
                self.assertEqual(drive.call_count, count)
                self.assertEqual(factory.return_value.__enter__.return_value.chromium.launch.return_value.close.call_count, count)

    def test_parse_preserves_password_and_deduplicates_email(self):
        rows = bulk.parse_accounts('# comment\nPerson@example.com|a password|JBSWY3DPEHPK3PXP\nperson@example.com|duplicate\nnext@example.com\twith spaces\n')
        self.assertEqual(rows, [('Person@example.com', 'a password', 'JBSWY3DPEHPK3PXP'), ('next@example.com', 'with spaces', '')])
        with self.assertRaisesRegex(ValueError, 'Line 1'):
            bulk.parse_accounts('not-an-email|secret')

    def test_email_is_from_auth_not_manual_name(self):
        self.assertEqual(hub.auth_email(auth()), 'person@example.com')
        self.assertIsNone(hub.auth_email({'tokens': {'id_token': 'broken'}}))
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'default' / 'sessions').mkdir(parents=True)
            store = hub.Store(base / 'data')
            try:
                store.settings['default_home'] = str(base / 'default')
                account = store.create('Old manual name', 'keep note')
                hub.atomic_write(store.profile(account) / 'auth.json', json.dumps(auth()).encode())
                store.sync_identity(account)
                self.assertEqual(account['name'], 'person@example.com')
                self.assertEqual(account['note'], 'keep note')
                store.save_accounts()
                with self.assertRaisesRegex(ValueError, 'already'):
                    store.import_auth(auth(), 'PERSON@example.com')
                with self.assertRaisesRegex(ValueError, 'match'):
                    store.import_auth(auth('other@example.com'), 'wrong@example.com')
                imported = store.import_auth(auth('new@example.com'), 'new@example.com')
                self.assertEqual(imported['name'], 'new@example.com')
                self.assertTrue((store.profile(imported) / 'auth.json').is_file())
                self.assertEqual(len(store.accounts), 2)
            finally:
                store.close()

    def test_tokens_require_full_oauth_result_and_matching_email(self):
        tokens = auth()['tokens']
        self.assertEqual(bulk.tokens_to_auth(tokens, 'person@example.com')['tokens']['account_id'], 'account-1')
        with self.assertRaises(ValueError):
            bulk.tokens_to_auth(dict(tokens, refresh_token=''), 'person@example.com')
        with self.assertRaises(ValueError):
            bulk.tokens_to_auth(tokens, 'wrong@example.com')

    def test_callback_validates_exact_origin_state_and_single_code(self):
        self.assertEqual(bulk.callback_code('http://localhost:1455/auth/callback?state=expected&code=abc', 'expected'), 'abc')
        for url in ['http://localhost:1455/auth/callback?state=wrong&code=abc', 'https://evil.example/auth/callback?state=expected&code=abc', 'http://localhost:1455/auth/callback?state=expected&code=a&code=b']:
            with self.assertRaises(ValueError):
                bulk.callback_code(url, 'expected')

    def test_batch_isolates_failures_and_honors_cancellation(self):
        events = []
        def login(credentials, cancel, progress):
            progress('Browser opened')
            if credentials[0] == 'bad@example.com':
                raise ValueError('Login rejected')
            return auth(credentials[0])
        rows = [('ok@example.com', 'secret', ''), ('bad@example.com', 'secret', '')]
        with patch.object(bulk, 'login_one', side_effect=login):
            bulk.run_batch(rows, 2, threading.Event(), lambda *event: events.append(event))
        self.assertEqual(rows, [])
        self.assertTrue(any(i == 0 and value is not None for i, status, value in events))
        self.assertTrue(any(i == 1 and status == 'Login rejected' and value is None for i, status, value in events))
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(bulk, 'login_one', side_effect=AssertionError('Cancelled account must not log in')):
            events.clear()
            bulk.run_batch([('ok@example.com', 'secret', '')], 1, cancelled, lambda *event: events.append(event))
        self.assertEqual(events, [(0, 'Cancelled', None)])


if __name__ == '__main__':
    unittest.main()
