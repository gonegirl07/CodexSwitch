import copy
import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import pools


class DynamicQuotaTests(unittest.TestCase):
    def test_old_pool_config_preserved_and_new_thresholds_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = pools.load_config(root)
            pools.save_config(root, config)
            original = (root / 'pools.json').read_bytes()
            loaded = pools.load_config(root)
            self.assertEqual(loaded, config)
            self.assertEqual((root / 'pools.json').read_bytes(), original)
            for field in ('min_30d', 'min_other'):
                for value in (-1, 101, float('nan'), True):
                    invalid = copy.deepcopy(config)
                    invalid['pools'][0][field] = value
                    with self.assertRaises(ValueError):
                        pools.validate_config(invalid)

    def test_same_duration_windows_both_enforced_and_unknown_duration(self):
        data = {'rate_limit': {'primary_window': {'used_percent': 10, 'limit_window_seconds': 604800, 'reset_at': 900},
                               'secondary_window': {'used_percent': 99, 'limit_window_seconds': 604800, 'reset_at': 900}}}
        quota = pools.normalize_usage(data, now=100)
        self.assertEqual(len(quota['windows']), 2)
        self.assertFalse(pools.eligible(quota, {'min_weekly': 5}, now=101))
        data['rate_limit'].pop('secondary_window')
        data['rate_limit']['primary_window'].pop('limit_window_seconds')
        quota = pools.normalize_usage(data, now=100)
        self.assertTrue(pools.eligible(quota, {'min_other': 5}, now=101))
        self.assertFalse(pools.eligible(quota, {'min_other': 90}, now=101))

    def test_real_window_shapes_and_pool_rotation(self):
        for duration, name in [(18000, '5h'), (604800, 'weekly'), (2592000, '30d'), (172800, '2d')]:
            with self.subTest(duration=duration):
                data = {'rate_limit': {'allowed': True, 'primary_window': {
                    'used_percent': 10, 'limit_window_seconds': duration, 'reset_at': 900}}}
                quota = pools.normalize_usage(data, now=100)
                self.assertIn(name, quota['windows'])
                policy = {'min_5h': 5, 'min_weekly': 5, 'min_30d': 5, 'min_other': 5}
                self.assertTrue(pools.eligible(quota, policy, now=101))
                quota['windows'][name]['remaining'] = 5
                self.assertFalse(pools.eligible(quota, policy, now=101))

    def test_malformed_present_window_must_not_be_treated_as_absent(self):
        good = {'used_percent': 10, 'limit_window_seconds': 604800, 'reset_at': 900}
        for bad in [{}, {'used_percent': None}, 'invalid']:
            quota = pools.normalize_usage({'rate_limit': {'primary_window': good, 'secondary_window': bad}}, now=100)
            self.assertFalse(pools.eligible(quota, {'min_5h': 0, 'min_weekly': 5}, now=101))
        for duration in (-60, 0, 'garbage'):
            quota = pools.normalize_usage({'rate_limit': {'primary_window': dict(good, limit_window_seconds=duration)}}, now=100)
            self.assertFalse(pools.eligible(quota, {'min_other': 5}, now=101))

    def test_legacy_lines_dynamic_labels_and_reserve_separation(self):
        self.assertTrue(hasattr(hub, 'account_quota_windows'), 'Missing dynamic quota reader')
        windows = hub.account_quota_windows({'quota': [
            'gpt-reserve · Weekly: 100% left',
            'codex · 43200 minutes: 85% left · reset 10/10 11:04']})
        self.assertEqual([(w['label'], w['remaining']) for w in windows], [('30d', 85)])
        self.assertEqual(hub.account_quota_windows({'quota': ['Reset count: 0']}), [])
        self.assertEqual(hub.account_quota_windows({'quota': ['codex · Spending: 90% left · 10 / 100']}), [])

    def test_structured_windows_keep_identity_and_unknown_duration(self):
        self.assertTrue(hasattr(hub, 'quota_windows'), 'Missing structured quota reader')
        result = {'rateLimits': {'primary': {'usedPercent': 23, 'windowDurationMins': 43200, 'resetsAt': 900}, 'secondary': None}}
        windows = hub.quota_windows(result)
        self.assertEqual([(w['id'], w['label'], w['remaining']) for w in windows], [('primary', '30d', 77)])
        result['rateLimits']['primary'].pop('windowDurationMins')
        self.assertEqual(hub.quota_windows(result)[0]['label'], 'Unknown')


if __name__ == '__main__':
    unittest.main()
