import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import pools
from test_bulk import auth


class SwitchTests(unittest.TestCase):
    def test_pool_switch_then_account_preserves_unrelated_config_and_backups(self):
        self.assertIsNotNone(importlib.util.find_spec('local_switch'), 'Local pool switching missing')
        import local_switch
        import tomlkit
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'home/sessions').mkdir(parents=True)
            home = root/'home'
            original = '# Keep comment\nmodel="native-test"\nprofile="work"\n[profiles.work]\nmodel_provider="old-provider"\nmodel_reasoning_effort="high"\n[projects."/tmp"]\ntrust_level="trusted"\n'
            hub.atomic_write(home/'config.toml', original.encode())
            hub.atomic_write(home/'auth.json', json.dumps(auth()).encode())
            store = hub.Store(root/'data')
            store.settings['default_home'] = str(home)
            try:
                account = store.create('x', '', auth_data=json.dumps(auth('next@example.com')).encode())
                config = pools.load_config(store.root)
                first = local_switch.switch_pool(store, config['pools'][0])
                doc = tomlkit.parse((home/'config.toml').read_text())
                provider = doc['model_providers'][doc['model_provider']]
                self.assertEqual(provider['base_url'], 'http://127.0.0.1:8311/v1')
                self.assertEqual(doc['profiles']['work']['model_provider'], doc['model_provider'])
                self.assertEqual(provider['experimental_bearer_token'], config['pools'][0]['key'])
                self.assertEqual(doc['model'], 'native-test')
                self.assertEqual((first/'config.toml').read_text(), original)
                local_switch.switch_pool(store, config['pools'][1])
                local_switch.switch_account(store, account)
                doc = tomlkit.parse((home/'config.toml').read_text())
                self.assertEqual(doc['model_provider'], 'openai')
                self.assertEqual(doc['profiles']['work']['model_provider'], 'openai')
                self.assertEqual(doc['profiles']['work']['model_reasoning_effort'], 'high')
                self.assertEqual(doc['projects']['/tmp']['trust_level'], 'trusted')
                self.assertIn('# Keep comment', (home/'config.toml').read_text())
                self.assertEqual(hub.auth_email(json.loads((home/'auth.json').read_bytes())), 'next@example.com')
                self.assertEqual((home/'config.toml').stat().st_mode & 0o777, 0o600)
            finally:
                store.close()
