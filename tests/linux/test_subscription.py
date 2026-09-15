import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import local_switch
from test_bulk import auth, token


class SubscriptionTests(unittest.TestCase):
    def test_subscription_expiry_uses_explicit_claim_only(self):
        credential = auth()
        credential['tokens']['id_token'] = token({'email': 'person@example.com', 'exp': 9999999999,
            'https://api.openai.com/auth': {'chatgpt_subscription_active_until': '2030-01-01T00:00:00Z'}})
        account = self.store.create('x', '', auth_data=json.dumps(credential).encode())
        self.assertEqual(account.get('subscription_active_until'), 1893456000)
        hub.atomic_write(self.store.profile(account)/'auth.json', json.dumps(auth()).encode())
        self.store.sync_identity(account)
        self.assertEqual(account['subscription_active_until'], 1893456000)
        fresh = self.store.create('x', '', auth_data=json.dumps(auth()).encode())
        self.assertNotIn('subscription_active_until', fresh)

    def test_subscription_remaining_boundaries(self):
        now = 1800000000
        for seconds, expected in [(30*86400, '30d'), (29*86400+1, '29d'), (86400, '1d'),
                                  (86399, '23h'), (5*3600, '5h'), (3600, '1h'),
                                  (3599, '<1h'), (0, 'Expired'), (-1, 'Expired')]:
            self.assertEqual(hub.subscription_remaining({'subscription_active_until': now+seconds}, now), expected)
        for value in (None, '', [], True, float('nan')):
            self.assertEqual(hub.subscription_remaining({'subscription_active_until': value}, now), '--')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'home/sessions').mkdir(parents=True)
        self.store = hub.Store(self.root/'data')
        self.addCleanup(self.store.close)
        self.store.settings['default_home'] = str(self.root/'home')

    def test_import_plan_and_preserve_last_known(self):
        for raw, expected in [('free', 'Free'), ('plus', 'Plus'), ('pro', 'Pro'),
                              ('pro_5x', 'Pro5x'), ('pro_20x', 'Pro20x'),
                              ('team', 'Business'), ('business', 'Business'),
                              ('enterprise', 'Enterprise'), (None, 'Unknown')]:
            credential = auth()
            credential['tokens']['id_token'] = token({'email': 'person@example.com',
                'https://api.openai.com/auth': {'chatgpt_plan_type': raw}})
            account = self.store.create('ignored', '', auth_data=json.dumps(credential).encode())
            self.assertEqual(account.get('subscription', 'Unknown'), expected)
            hub.atomic_write(self.store.profile(account)/'auth.json', json.dumps(auth()).encode())
            self.store.sync_identity(account)
            self.assertEqual(account.get('subscription', 'Unknown'), expected)

    def test_local_current_uses_identity_and_effective_provider(self):
        first = self.store.create('x', '', auth_data=json.dumps(auth()).encode())
        second = self.store.create('x', '', auth_data=json.dumps(auth('other@example.com')).encode())
        local_switch.switch_account(self.store, first)
        self.assertEqual(local_switch.current_account(self.store), first['id'])
        local_switch.switch_account(self.store, second)
        self.assertEqual(local_switch.current_account(self.store), second['id'])
        hub.atomic_write(self.root/'home/config.toml', b'profile="pool"\n[profiles.pool]\nmodel_provider="codex_hub_pool_1"\n')
        self.assertIsNone(local_switch.current_account(self.store))
        hub.atomic_write(self.root/'home/config.toml', b'invalid [')
        self.assertIsNone(local_switch.current_account(self.store))
        hub.atomic_write(self.root/'home/config.toml', b'profile="missing"\n')
        self.assertIsNone(local_switch.current_account(self.store))

    def test_quota_metadata_and_old_token_do_not_undo_new_tier(self):
        credential = auth()
        credential['tokens']['id_token'] = token({'email': 'person@example.com',
            'https://api.openai.com/auth': {'chatgpt_plan_type': 'free'}})
        account = self.store.create('x', '', auth_data=json.dumps(credential).encode())
        metadata = {}
        lines = hub.profile_quota(str(Path(__file__).with_name('fake_codex.py')), self.store.profile(account), metadata)
        self.assertEqual(lines, ['codex · 5 hours: 60% left'])
        self.assertEqual(metadata['subscription'], 'Plus')
        self.assertEqual(metadata['quota_windows'][0]['remaining'], 60)
        account.update(metadata)
        self.store.sync_identity(account)
        self.assertEqual(account['subscription'], 'Plus')
        self.store.save_accounts()
        self.assertEqual(json.loads((self.store.root/'accounts.json').read_bytes())['accounts'][0]['subscription'], 'Plus')

    def test_unknown_and_malformed_metadata_are_not_free(self):
        for raw in ('', 'new_undocumented_tier', None, [], 5):
            self.assertIsNone(hub.subscription_name(raw))
        self.assertIsNone(hub.auth_subscription({'tokens': {'id_token': 'malformed'}}))
        account = self.store.create('x', '', auth_data=json.dumps(auth()).encode())
        account['quota'] = ['codex · 5 hours: 100% left']
        self.store.sync_identity(account)
        self.assertNotIn('subscription', account)

    def test_native_plan_variants_do_not_guess_multiplier(self):
        for raw, expected in [('prolite', 'Pro Lite'), ('self_serve_business_prolite', 'Business'),
                              ('self_serve_business_usage_based', 'Business'), ('ent26', 'Enterprise'),
                              ('enterprise_cbp_automation', 'Enterprise'), ('enterprise_cbp_usage_based', 'Enterprise'),
                              ('edu_plus', 'Edu Plus'), ('edu_pro', 'Edu Pro')]:
            self.assertEqual(hub.subscription_name(raw), expected)

    def test_pool_tags_require_fresh_positive_counts(self):
        document = {'pid': os.getpid(), 'updated_at': time.time(),
                    'inflight_by_pool': {'0': {'one': 2}, '1': {'one': 1, 'idle': 0}}}
        path = self.store.root/'pool-status.json'
        hub.atomic_write(path, json.dumps(document).encode())
        self.assertEqual(local_switch.pool_activity(self.store), {'one': ['current pool 1', 'current pool 2']})
        document['updated_at'] -= 30
        hub.atomic_write(path, json.dumps(document).encode())
        self.assertEqual(local_switch.pool_activity(self.store), {})
