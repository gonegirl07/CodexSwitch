import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub


class HubTests(unittest.TestCase):
    def test_codex_discovery_from_desktop_path_preserves_explicit_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            executable = home / '.local/bin/codex'
            executable.parent.mkdir(parents=True)
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o755)
            with patch('hub.Path.home', return_value=home), patch.dict(os.environ, {'PATH': '/nonexistent'}):
                self.assertEqual(hub.codex_path({'codex': 'codex'}), str(executable))
                with self.assertRaises(ValueError):
                    hub.codex_path({'codex': '/missing/custom/codex'})
                self.assertEqual(hub.codex_path({'codex': str(executable)}), str(executable))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'default'
        (self.home / 'sessions').mkdir(parents=True)
        self.config = 'model = "test"\ncli_auth_credentials_store = "keyring" # keep\n[projects."/tmp"]\ntrust_level = "trusted"\n'
        (self.home / 'config.toml').write_text(self.config)
        (self.home / 'auth.json').write_text('{"test": "original"}')
        self.store = hub.Store(self.base / 'manager')
        self.addCleanup(self.store.close)
        self.store.settings['default_home'] = str(self.home)

    def test_create_apply_and_delete_preserve_shared_files(self):
        session = self.home / 'sessions' / 'one.jsonl'
        session.write_text('shared')
        account = self.store.create('Work', 'note', copy_auth=True)
        profile = self.store.profile(account)
        self.assertEqual((profile / 'auth.json').read_text(), '{"test": "original"}')
        self.assertEqual((self.home / 'config.toml').read_text(), self.config)
        self.assertIn('"file" # keep', (profile / 'config.toml').read_text())
        self.assertEqual((profile / 'sessions').resolve(), self.home / 'sessions')
        self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
        self.assertEqual((profile / 'auth.json').stat().st_mode & 0o777, 0o600)
        (profile / 'auth.json').write_text('{"test": "new"}')
        self.store.apply(account)
        self.assertEqual(json.loads((self.home / 'auth.json').read_text()), {'test': 'new'})
        self.store.delete(account)
        self.assertFalse(profile.exists())
        self.assertEqual(session.read_text(), 'shared')
        self.assertEqual(self.store.accounts, [])

    def test_new_login_does_not_copy_auth(self):
        account = self.store.create('New', '')
        self.assertFalse((self.store.profile(account) / 'auth.json').exists())

    def test_delete_unlinks_extra_symlinks_without_following_them(self):
        account = self.store.create('Work', '')
        (self.store.profile(account) / 'outside').symlink_to(self.home, target_is_directory=True)
        self.store.delete(account)
        self.assertTrue((self.home / 'auth.json').exists())

    def test_retargeted_sessions_and_profile_traversal_rejected(self):
        account = self.store.create('Work', '')
        link = self.store.profile(account) / 'sessions'
        link.unlink()
        link.symlink_to(self.base)
        with self.assertRaises(ValueError):
            self.store.delete(account)
        with self.assertRaises(ValueError):
            self.store.profile({'id': '../../default'})

    def test_login_secrets_round_trip_and_are_removed_with_account(self):
        account = self.store.create('Work', '')
        self.assertIsNone(self.store.load_login(account))
        self.store.save_login(account, 'person@example.com', 'secret-pass', 'JBSWY3DPEHPK3PXP')
        path = self.store.profile(account) / 'login.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['password'], 'secret-pass')
        self.assertNotIn('secret-pass', json.dumps({'version': 1, 'accounts': self.store.accounts}))
        self.assertEqual(self.store.load_login(account)['totp'], 'JBSWY3DPEHPK3PXP')
        self.store.delete(account)
        self.assertFalse(path.exists())

    def test_pending_login_keeps_saved_or_existing_email(self):
        account = self.store.create('Work', '')
        self.assertEqual(account['name'], 'Pending login')
        self.store.save_login(account, 'keep@example.com', 'secret-pass', '')
        self.store.sync_identity(account)
        self.assertEqual(account['email'], 'keep@example.com')
        self.assertEqual(account['name'], 'keep@example.com')
        account.update(name='Pending login', email=None)
        self.store.sync_identity(account)
        self.assertEqual(account['email'], 'keep@example.com')
        leftover = self.store.create('left@example.com', '')
        leftover.update(email='left@example.com', name='Pending login')
        self.store.sync_identity(leftover)
        self.assertEqual(leftover['name'], 'left@example.com')
        self.assertEqual(leftover['email'], 'left@example.com')

    def test_priority_persists_and_login_required_detects_disconnect(self):
        account = self.store.create('Work', 'note')
        self.assertEqual(hub.account_priority_value(account), 1)
        self.assertEqual(hub.account_priority_value({}), 1)
        self.assertEqual(hub.account_priority_value({'priority': 0}), 0)
        self.assertEqual(hub.account_priority_value({'priority': 4}), 4)
        self.assertEqual(hub.account_priority_value({'priority': 5}), 1)
        self.assertEqual(hub.account_priority_value({'priority': -1}), 1)
        self.assertEqual(hub.account_priority_value({'priority': '2'}), 1)
        account['priority'] = 3
        self.store.save_accounts()
        saved = json.loads((self.store.root / 'accounts.json').read_text())
        self.assertEqual(hub.account_priority_value(saved['accounts'][0]), 3)
        self.assertTrue(hub.login_required({'status': 'Not logged in', 'quota_error': 'Log in before reading quota.'}))
        self.assertFalse(hub.login_required({'status': 'Logged in (local credentials)', 'quota_error': None}))
        self.assertFalse(hub.login_required({'status': 'Logged in (local credentials)', 'quota_error': 'quota_unavailable'}))

    def test_error_detail_formats_http_codes_and_auth_relogin_gate(self):
        cockpit = 'Token refresh failed: status=401 Unauthorized, error_code=Some("refresh_token_reused")'
        self.assertEqual(hub.error_detail(cockpit)['http'], 401)
        self.assertEqual(hub.error_detail(cockpit)['code'], 'refresh_token_reused')
        self.assertEqual(hub.format_error_label({'quota_error': cockpit}), '401 · refresh_token_reused')
        rpc = {'code': -32603, 'message': 'unauthorized', 'data': {'http_status': 401, 'error_code': 'token_invalidated'}}
        self.assertEqual(hub.format_error_label(rpc), '401 · token_invalidated')
        self.assertEqual(hub.format_error_label({'error': {'message': '403 forbidden'}}), '403 · forbidden')
        self.assertTrue(hub.login_required({'quota_error': '401 · token_invalidated'}))
        self.assertTrue(hub.login_required({'quota_error': cockpit}))
        self.assertFalse(hub.login_required({'quota_error': '403 · forbidden'}))
        self.assertFalse(hub.login_required({'quota_error': 'offline'}))
        self.assertFalse(hub.login_required({'quota_error': 'Quota request timed out; try Refresh again.'}))

    def test_replace_auth_updates_profile_without_new_account(self):
        from test_bulk import auth
        account = self.store.create('Work', '', auth_data=json.dumps(auth('old@example.com')).encode())
        self.store.replace_auth(account, auth('old@example.com'))
        self.assertEqual(len(self.store.accounts), 1)
        self.assertEqual(hub.auth_email(json.loads((self.store.profile(account) / 'auth.json').read_text())),
                         'old@example.com')
        with self.assertRaises(ValueError):
            self.store.replace_auth(account, auth('other@example.com'))

    def test_invalid_auth_does_not_replace_default(self):
        account = self.store.create('Work', '')
        (self.store.profile(account) / 'auth.json').write_text('[]')
        with self.assertRaises(ValueError):
            self.store.apply(account)
        self.assertEqual((self.home / 'auth.json').read_text(), '{"test": "original"}')

    def test_single_instance_and_invalid_document(self):
        with self.assertRaises(RuntimeError):
            hub.Store(self.store.root)
        self.store.close()
        (self.store.root / 'accounts.json').write_text('{"version":99,"accounts":[]}')
        with self.assertRaises(ValueError):
            hub.Store(self.store.root)

    def test_config_edit_preserves_multiline_prompt_and_nested_setting(self):
        text = 'instructions = """\ncli_auth_credentials_store = "auto"\n"""\n[other]\ncli_auth_credentials_store = "keyring"\n'
        self.assertEqual(hub.file_config(text), 'cli_auth_credentials_store = "file"\n' + text)
        with self.assertRaises(ValueError):
            hub.file_config('cli_auth_credentials_store="auto"\ncli_auth_credentials_store="file"\n')

    def test_environment_isolation(self):
        env = hub.isolated_env(self.home, {'PATH': '/usr/bin', 'OPENAI_API_KEY': 'secret', 'CODEX_HOME': '/wrong', 'CODEX_REMOTE': 'wrong'})
        self.assertEqual(env['CODEX_HOME'], str(self.home))
        self.assertEqual(env['PATH'], '/usr/bin')
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('CODEX_REMOTE', env)

    def test_project_catalog_bounds_reads_and_preserves_case(self):
        sessions = self.home / 'sessions'
        for i, cwd in enumerate(['/tmp/Repo', '/tmp/repo', '/tmp/Repo', 'C:\\Windows']):
            (sessions / f'{i}.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'cwd': cwd}}) + '\n')
        (sessions / 'huge.jsonl').write_text('x' * (128 * 1024 + 1))
        (sessions / 'loop').symlink_to(sessions)
        paths, skipped = hub.projects(sessions)
        self.assertEqual(set(paths), {'/tmp/Repo', '/tmp/repo'})
        self.assertEqual(skipped, 2)

    def test_quota_windows_and_duplicate_bucket(self):
        bucket = {'limitId': 'codex', 'primary': {'usedPercent': 25, 'windowDurationMins': 300, 'resetsAt': 2000000000}, 'secondary': {'usedPercent': 100, 'windowDurationMins': 10080}, 'credits': {'unlimited': True}}
        lines = hub.quota_lines({'rateLimitsByLimitId': {'codex': bucket}, 'rateLimits': bucket})
        self.assertEqual(len(lines), 3)
        self.assertIn('75%', lines[0])
        self.assertIn('0%', lines[1])
        self.assertIn('Unlimited', lines[2])

    def test_quota_selection_never_uses_reserve_for_codex(self):
        lines = ['gpt-reserve · Weekly: 100% left', 'codex · Weekly: 23% left']
        self.assertEqual(hub.quota_remaining(lines, 'Weekly'), 23)
        self.assertEqual(hub.quota_remaining(lines[::-1], 'Weekly'), 23)
        self.assertEqual(hub.quota_remaining(lines, 'Weekly', 'gpt-reserve'), 100)
        self.assertIsNone(hub.quota_remaining(lines[:1], 'Weekly'))
        self.assertIsNone(hub.quota_remaining(lines, '5 hours'))

    def test_quota_labels_follow_provider_identity(self):
        lines = hub.quota_lines({'rateLimitsByLimitId': {
            'base_model_inference': {'limitName': 'GPT Reserve', 'primary': {'usedPercent': 0, 'windowDurationMins': 10080}},
            'codex': {'limitName': 'Other display label', 'primary': {'usedPercent': 77, 'windowDurationMins': 10080}}}})
        self.assertEqual(hub.quota_remaining(lines, 'Weekly'), 23)
        self.assertEqual(hub.quota_remaining(lines, 'Weekly', 'gpt-reserve'), 100)

    def test_reset_count_unknown_is_not_zero(self):
        for lines, expected in [([], None), (['Reset count: 0'], 0),
                                (['Reset count: 3'], 3), (['Reset count: -1'], None)]:
            self.assertEqual(hub.reset_count(lines), expected)

    def test_reset_protocol_consumes_once_and_reads_updated_quota(self):
        (self.home / 'reset-test').touch()
        fixture = str(Path(__file__).parent / 'fake_codex.py')
        metadata = {}
        lines = hub.read_quota(fixture, self.home, metadata=metadata, reset_key='test-reset-key')
        self.assertEqual(metadata['reset_outcome'], 'reset')
        self.assertEqual(hub.reset_count(lines), 0)
        self.assertEqual(hub.quota_remaining(lines, '5 hours'), 100)

    def test_reset_protocol_no_credits_does_not_consume(self):
        (self.home / 'reset-test').touch()
        (self.home / 'no-credits').touch()
        metadata = {}
        lines = hub.read_quota(str(Path(__file__).parent / 'fake_codex.py'), self.home,
                               metadata=metadata, reset_key='test-reset-key')
        self.assertEqual(metadata['reset_outcome'], 'noCredit')
        self.assertEqual(hub.reset_count(lines), 0)

    def test_reset_protocol_outcomes_and_post_reset_read_failure(self):
        fixture = str(Path(__file__).parent / 'fake_codex.py')
        (self.home / 'reset-test').touch()
        for outcome in ('nothingToReset', 'noCredit', 'alreadyRedeemed'):
            (self.home / 'reset-outcome').write_text(outcome)
            metadata = {}
            hub.read_quota(fixture, self.home, metadata=metadata, reset_key='test-reset-key')
            self.assertEqual(metadata['reset_outcome'], outcome)
        (self.home / 'reset-outcome').write_text('reset')
        (self.home / 'reset-read-fail').touch()
        metadata = {}
        with self.assertRaises(ValueError):
            hub.read_quota(fixture, self.home, metadata=metadata, reset_key='test-reset-key')
        self.assertEqual(metadata['reset_outcome'], 'reset')

    def test_reset_unknown_count_does_not_consume(self):
        with self.assertRaisesRegex(ValueError, 'Reset count unavailable'):
            hub.read_quota(str(Path(__file__).parent / 'fake_codex.py'), self.home,
                           metadata={}, reset_key='test-reset-key')

    def test_profile_reset_keeps_isolation_and_preserves_quota_on_failure(self):
        (self.home / 'auth.json').write_text('{"quota_test":true}')
        def reset_rpc(executable, home, metadata=None, reset_key=None):
            self.assertEqual(reset_key, 'same-key')
            self.assertNotEqual(home, self.home)
            self.assertFalse((home / 'sessions').exists())
            self.assertEqual((home / 'config.toml').read_text(), 'cli_auth_credentials_store = "file"\n')
            (home / 'auth.json').write_text('{"quota_test":true,"refreshed":true}')
            metadata['reset_outcome'] = 'reset'
            raise TimeoutError('quota read timed out')
        with patch('hub.read_quota', side_effect=reset_rpc):
            update = hub.reset_quota('unused', self.home, 'same-key')
        self.assertNotIn('quota', update)
        self.assertEqual(update['reset_outcome'], 'reset')
        self.assertIn('quota_error', update)
        self.assertTrue(json.loads((self.home / 'auth.json').read_text())['refreshed'])

    def test_terminal_helper_runs_cli_in_isolated_profile(self):
        fixture = Path(__file__).parent / 'fake_codex.py'
        work = self.base / "project ' with $special; characters"
        work.mkdir()
        result = subprocess.run([sys.executable, str(Path(hub.__file__)), '--terminal', str(fixture), str(self.home), str(work), 'login'], capture_output=True, env=dict(os.environ, OPENAI_API_KEY='synthetic-test-key'))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads((self.home / 'terminal-check.json').read_text()), {'cwd': str(work), 'home': str(self.home)})

    def test_quota_protocol_and_timeout(self):
        fixture = Path(__file__).parent / 'fake_codex.py'
        (self.home / 'rpc-error.json').write_text(json.dumps({
            'message': 'unauthorized', 'data': {'http_status': 401, 'error_code': 'token_invalidated'}}))
        with self.assertRaises(ValueError) as raised:
            hub.read_quota(str(fixture), self.home, timeout=2)
        self.assertEqual(str(raised.exception), '401 · token_invalidated')
        (self.home / 'rpc-error.json').unlink()
        lines = hub.read_quota(str(fixture), self.home, timeout=2)
        self.assertEqual(lines, ['codex · 5 hours: 60% left'])
        (self.home / 'stall').touch()
        with self.assertRaises(TimeoutError):
            hub.read_quota(str(fixture), self.home, timeout=0.1)

    def test_profile_quota_avoids_sessions_and_retains_refreshed_auth(self):
        fixture = Path(__file__).parent / 'fake_codex.py'
        (self.home / 'auth.json').write_text('{"quota_test": true}')
        (self.home / 'state_5.sqlite').write_bytes(b'do not touch')
        config = 'sqlite_home = "/do/not/use"\n'
        (self.home / 'config.toml').write_text(config)
        lines = hub.profile_quota(str(fixture), self.home)
        self.assertEqual(lines, ['codex · 5 hours: 60% left'])
        self.assertTrue(json.loads((self.home / 'auth.json').read_text())['refreshed'])
        self.assertEqual((self.home / 'state_5.sqlite').read_bytes(), b'do not touch')
        self.assertEqual((self.home / 'config.toml').read_text(), config)
        self.assertTrue((self.home / 'sessions').is_dir())
        self.assertEqual(list(self.home.glob('.quota-*')), [])

    def test_open_path_uses_platform_opener(self):
        with patch('hub.subprocess.Popen') as popen, patch('hub.sys.platform', 'darwin'):
            hub.open_path('/tmp')
            self.assertEqual(popen.call_args[0][0][:2], ['open', '/tmp'])
        with patch('hub.subprocess.Popen') as popen, patch('hub.sys.platform', 'linux'):
            hub.open_path('/tmp')
            self.assertEqual(popen.call_args[0][0][:2], ['xdg-open', '/tmp'])

    def test_launch_opens_macos_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            with patch('hub.sys.platform', 'darwin'), patch('hub.subprocess.Popen') as popen:
                hub.launch('codex', work, work, 'open')
            args = popen.call_args[0][0]
            self.assertEqual(args[:3], ['open', '-a', 'Terminal'])
            self.assertTrue(args[3].endswith('.command'))


if __name__ == '__main__':
    unittest.main()
