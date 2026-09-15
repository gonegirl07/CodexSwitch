import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import pools


class PoolTests(unittest.TestCase):
    def test_network_selection_tracks_interface_not_saved_ip(self):
        self.assertTrue(hasattr(pools, 'bind_hosts'), 'Interface-based listeners not implemented')
        policy = {'interface': 'wifi0'}
        networks = [{'interface': 'eth0', 'addresses': ['192.168.1.2']}, {'interface': 'wifi0', 'addresses': ['192.168.2.3']}]
        self.assertEqual(pools.bind_hosts(policy, networks), ['127.0.0.1', '192.168.2.3'])
        networks[1]['addresses'] = ['192.168.2.9']
        self.assertEqual(pools.bind_hosts(policy, networks), ['127.0.0.1', '192.168.2.9'])
        self.assertEqual(pools.bind_hosts(policy, []), ['127.0.0.1'])
        self.assertEqual(pools.bind_hosts({}, networks), ['127.0.0.1'])

    def test_network_config_rejects_wildcard_and_migrates_local_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = pools.load_config(Path(temporary))
            config['pools'][0]['interface'] = '*'
            with self.assertRaises(ValueError):
                pools.validate_config(config)

    def test_config_round_trip_and_invalid_ports_members_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = pools.load_config(root)
            self.assertEqual([p['port'] for p in config['pools']], [8311, 8312])
            self.assertNotEqual(config['pools'][0]['key'], config['pools'][1]['key'])
            pools.save_config(root, config)
            self.assertEqual(pools.load_config(root), config)
            self.assertEqual((root / 'pools.json').stat().st_mode & 0o777, 0o600)
            for field, value in [('port', 8312), ('min_5h', -1), ('min_weekly', 101), ('members', ['../auth']), ('key', '')]:
                broken = copy.deepcopy(config)
                broken['pools'][0][field] = value
                with self.assertRaises(ValueError):
                    pools.save_config(root, broken)
            self.assertEqual(pools.load_config(root), config)

    def test_add_pool_keeps_existing_two_and_assigns_next_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = pools.load_config(root)
            third = pools.add_pool(config)
            self.assertEqual(third['id'], 'pool-3')
            self.assertEqual(third['port'], 8313)
            self.assertEqual(third['members'], [])
            self.assertNotIn(third['key'], {config['pools'][0]['key'], config['pools'][1]['key']})
            pools.save_config(root, config)
            loaded = pools.load_config(root)
            self.assertEqual([p['id'] for p in loaded['pools']], ['pool-1', 'pool-2', 'pool-3'])
            self.assertEqual([p['port'] for p in loaded['pools']], [8311, 8312, 8313])
            self.assertEqual(loaded['pools'][0]['key'], config['pools'][0]['key'])
            one = copy.deepcopy(loaded)
            one['pools'] = one['pools'][:1]
            with self.assertRaises(ValueError):
                pools.validate_config(one)

    def test_remove_pool_reindexes_and_keeps_minimum_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = pools.load_config(root)
            third = pools.add_pool(config)
            member = 'ab' * 16
            config['pools'][0]['members'] = [member]
            third['port'] = 8319
            removed = pools.remove_pool(config, 1)
            self.assertEqual(removed['id'], 'pool-2')
            self.assertEqual([p['id'] for p in config['pools']], ['pool-1', 'pool-2'])
            self.assertEqual(config['pools'][0]['members'], [member])
            self.assertEqual(config['pools'][1]['port'], 8319)
            self.assertEqual(config['pools'][1]['key'], third['key'])
            with self.assertRaises(ValueError):
                pools.remove_pool(config, 0)
            with self.assertRaises(ValueError):
                pools.remove_pool(config, 9)
            pools.save_config(root, config)
            loaded = pools.load_config(root)
            self.assertEqual([p['id'] for p in loaded['pools']], ['pool-1', 'pool-2'])
            self.assertEqual(loaded['pools'][1]['port'], 8319)

    def test_ranked_members_use_lower_priority_random_ties_and_sticky_current(self):
        policy = {'members': ['aaa', 'bbb', 'ccc']}
        self.assertEqual(pools.ranked_members(policy, {'aaa': 4, 'bbb': 0, 'ccc': 1}),
                         ['bbb', 'ccc', 'aaa'])
        sticky = pools.ranked_members(policy, {'aaa': 1, 'bbb': 0, 'ccc': 1}, current='aaa')
        self.assertEqual(sticky[0], 'aaa')
        self.assertEqual(sticky[1:], ['bbb', 'ccc'])
        self.assertEqual(pools.ranked_members(policy, {}, current='bbb')[0], 'bbb')
        seen = {tuple(pools.ranked_members(policy, {'aaa': 1, 'bbb': 1, 'ccc': 1})) for _ in range(80)}
        self.assertGreater(len(seen), 1, 'Equal priorities must not keep stable list order')
        for order in seen:
            self.assertEqual(set(order), {'aaa', 'bbb', 'ccc'})

    def test_threshold_or_stale_unknown_and_reset(self):
        policy = {'min_5h': 10, 'min_weekly': 20}
        quota = {'checked_at': 100, 'windows': {
            '5h': {'remaining': 50, 'resets_at': 200},
            'weekly': {'remaining': 50, 'resets_at': 900}}}
        self.assertTrue(pools.eligible(quota, policy, now=110))
        for key, remaining in [('5h', 10), ('weekly', 20)]:
            changed = copy.deepcopy(quota)
            changed['windows'][key]['remaining'] = remaining
            self.assertFalse(pools.eligible(changed, policy, now=110))
        self.assertFalse(pools.eligible(quota, policy, now=201))
        self.assertFalse(pools.eligible(None, policy, now=110))
        self.assertFalse(pools.eligible(dict(quota, checked_at=0), policy, now=110))

    def test_normalize_live_usage_no_invented_100_percent(self):
        snapshot = pools.normalize_usage({'rate_limit': {
            'primary_window': {'used_percent': 42, 'limit_window_seconds': 18000, 'reset_at': 1000},
            'secondary_window': {'used_percent': 83, 'limit_window_seconds': 604800, 'reset_at': 2000}}}, now=100)
        self.assertEqual(snapshot['windows']['5h']['remaining'], 58)
        self.assertEqual(snapshot['windows']['weekly']['remaining'], 17)
        self.assertEqual(pools.normalize_usage({}, now=100)['windows'], {})


if __name__ == '__main__':
    unittest.main()
