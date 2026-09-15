"""Persistent pool policy and numeric usage; independent of HTTP and Tk."""
import json
import ipaddress
from pathlib import Path
import math
import random
import re
import secrets
import subprocess
import time

import hub

QUOTA_TTL = 60


class PoolError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def load_config(root):
    path = hub.ordinary(root / 'pools.json')
    if path.exists():
        config = json.loads(hub.read_file(path))
    else:
        config = {'version': 1, 'pools': [
            {'id': f'pool-{i}', 'port': 8310 + i, 'key': 'hub-' + secrets.token_urlsafe(32),
             'members': [], 'min_5h': 5, 'min_weekly': 5, 'image_model': ''}
            for i in (1, 2)]}
    validate_config(config)
    return config


def validate_config(config):
    if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('pools'), list) or len(config['pools']) < 2:
        raise ValueError('Pool configuration must contain at least two version-1 pools.')
    ports, keys = set(), set()
    for i, pool in enumerate(config['pools'], 1):
        if not isinstance(pool, dict) or pool.get('id') != f'pool-{i}':
            raise ValueError('Pool IDs must be pool-1, pool-2, … in order.')
        port = pool.get('port')
        if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
            raise ValueError('Choose different ports between 1024 and 65535.')
        ports.add(port)
        key = pool.get('key')
        if not isinstance(key, str) or not 24 <= len(key) <= 256 or re.search(r'\s', key) or key in keys:
            raise ValueError('Each pool requires a distinct API key of 24–256 non-space characters.')
        keys.add(key)
        members = pool.get('members')
        if not isinstance(members, list) or any(not isinstance(m, str) or not re.fullmatch(r'[0-9a-f]{32}', m) for m in members) or len(set(members)) != len(members):
            raise ValueError('Pool members must be unique profile IDs.')
        for field in ('min_5h', 'min_weekly', 'min_30d', 'min_other'):
            value = pool.get(field, 5) if field in ('min_30d', 'min_other') else pool.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Remaining quota thresholds must be between 0 and 100 percent.')
        if not isinstance(pool.get('image_model', ''), str) or len(pool.get('image_model', '')) > 120:
            raise ValueError('Invalid image response model.')
        interface = pool.get('interface', '')
        if not isinstance(interface, str) or (interface and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface)):
            raise ValueError('Select a network interface or Local only; wildcard binding is not supported.')


def save_config(root, config):
    validate_config(config)
    hub.atomic_write(root / 'pools.json', json.dumps(config, indent=2).encode())


def add_pool(config):
    validate_config(config)
    used = {pool['port'] for pool in config['pools']}
    port = 8310 + len(config['pools']) + 1
    while port in used:
        port += 1
        if port > 65535:
            raise ValueError('No unused port remains for another pool.')
    pool = {'id': f'pool-{len(config["pools"]) + 1}', 'port': port, 'key': 'hub-' + secrets.token_urlsafe(32),
            'members': [], 'min_5h': 5, 'min_weekly': 5, 'image_model': ''}
    config['pools'].append(pool)
    validate_config(config)
    return pool


def remove_pool(config, index):
    validate_config(config)
    if len(config['pools']) <= 2:
        raise ValueError('Keep at least two pools.')
    if type(index) is not int or not 0 <= index < len(config['pools']):
        raise ValueError('Choose an existing pool to delete.')
    removed = config['pools'].pop(index)
    for i, pool in enumerate(config['pools'], 1):
        pool['id'] = f'pool-{i}'
    validate_config(config)
    return removed


def ranked_members(policy, priorities, current=None):
    members = list(policy.get('members') or [])
    groups = {}
    for identity in members:
        value = priorities.get(identity, 1)
        if type(value) is not int:
            value = 1
        groups.setdefault(value, []).append(identity)
    ordered = []
    for value in sorted(groups):
        group = groups[value]
        random.shuffle(group)
        ordered.extend(group)
    if current in ordered:
        ordered = [current] + [identity for identity in ordered if identity != current]
    return ordered


def network_interfaces():
    """Current private IPv4 addresses; never enable public/wildcard listeners."""
    result = subprocess.run(['ip', '-j', '-4', 'address', 'show', 'up'], capture_output=True, text=True, check=True, timeout=3)
    networks = []
    for device in json.loads(result.stdout):
        name = device['ifname']
        addresses = []
        for item in device.get('addr_info', []):
            address = ipaddress.ip_address(item['local'])
            if item.get('scope') == 'global' and address.version == 4 and any(address in net for net in (
                    ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('192.168.0.0/16'))):
                addresses.append(str(address))
        if addresses:
            kind = 'Wi-Fi' if (Path('/sys/class/net') / name / 'wireless').exists() else 'Ethernet / LAN'
            networks.append({'interface': name, 'addresses': addresses, 'label': f'{kind} · {name} · {", ".join(addresses)}'})
    return networks


def bind_hosts(policy, networks):
    return ['127.0.0.1'] + list(dict.fromkeys(address for network in networks
        if network['interface'] == policy.get('interface', '') for address in network['addresses']))


def normalize_usage(data, now=None):
    windows = {}
    limits = data.get('rate_limit') or {}
    if not isinstance(limits, dict):
        limits = {}
    invalid = False
    for field in ('primary_window', 'secondary_window'):
        window = limits.get(field)
        if window is None:
            continue
        if not isinstance(window, dict):
            invalid = True
            continue
        seconds = window.get('limit_window_seconds')
        valid_duration = type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0
        if seconds is not None and not valid_duration:
            invalid = True
        name = hub.quota_window_label(seconds / 60).lower() if valid_duration else 'unknown'
        used = window.get('used_percent')
        reset = window.get('reset_at')
        if type(used) in (int, float) and math.isfinite(used) and type(reset) in (int, float) and math.isfinite(reset):
            key = name if name not in windows else name + ':' + field
            windows[key] = {'remaining': max(0, min(100, 100 - used)), 'resets_at': reset, 'period': name}
        else:
            invalid = True
    return {'checked_at': time.time() if now is None else now, 'windows': windows,
            'blocked': invalid or limits.get('allowed') is False or limits.get('limit_reached') is True}


def eligible(quota, policy, now=None):
    now = time.time() if now is None else now
    if not quota or quota.get('blocked') or not 0 <= now - quota.get('checked_at', 0) < QUOTA_TTL:
        return False
    windows = quota.get('windows') or {}
    if not windows:
        return False
    for name, window in windows.items():
        field = {'5h': 'min_5h', 'weekly': 'min_weekly', '30d': 'min_30d'}.get(window.get('period', name), 'min_other')
        threshold = policy.get(field, 5)
        if window['resets_at'] <= now or window['remaining'] <= threshold:
            return False
    return True


def quota_fresh(quota, now=None):
    now = time.time() if now is None else now
    return bool(quota and 0 <= now - quota['checked_at'] < QUOTA_TTL and
                all(w['resets_at'] > now for w in quota['windows'].values()))


def client_examples(pool, model='MODEL_FROM_V1_MODELS'):
    base = f"http://127.0.0.1:{pool['port']}/v1"
    name = pool['id'].replace('-', '_')
    return (
        f'# {pool["id"]}: set CODEX_POOL_KEY to the key copied from Hub\n'
        '# Add to your user-level Codex config (or a separate CODEX_HOME):\n'
        f'model_provider = "{name}"\nmodel = "{model}"\n\n'
        f'[model_providers.{name}]\nname = "{pool["id"]}"\n'
        f'base_url = "{base}"\nenv_key = "CODEX_POOL_KEY"\n'
        'wire_api = "responses"\nsupports_websockets = true\n\n'
        '# pi ~/.pi/agent/models.json (merge providers):\n' +
        json.dumps({'providers': {name: {'baseUrl': base, 'api': 'openai-responses',
          'apiKey': 'CODEX_POOL_KEY', 'models': [{'id': model, 'reasoning': True, 'input': ['text', 'image']}]}}}, indent=2) +
        '\n\n# OpenClaw config (merge models.providers):\n' +
        json.dumps({'models': {'providers': {name: {'baseUrl': base, 'api': 'openai-responses',
          'apiKey': '${CODEX_POOL_KEY}', 'models': [{'id': model, 'name': model, 'reasoning': True, 'input': ['text', 'image']}]}}}}, indent=2))
