"""Backed-up local Codex switching; keep unrelated TOML settings/comments."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

import hub


def current_account(store):
    """Read effective default provider/login without modifying credentials."""
    try:
        import tomlkit
        home = hub.ordinary(store.settings['default_home'])
        path = home/'config.toml'
        doc = tomlkit.parse(hub.read_file(path).decode('utf-8-sig')) if path.exists() else {}
        provider = doc.get('model_provider', 'openai')
        if doc.get('profile'):
            provider = doc['profiles'][doc['profile']].get('model_provider', provider)
        if provider != 'openai':
            return None
        auth = json.loads(hub.read_file(home/'auth.json'))
        identity = auth.get('tokens', {}).get('account_id')
        email = hub.auth_email(auth)
        if not identity or not email:
            return None
        matches = []
        for account in store.accounts:
            try:
                candidate = json.loads(hub.read_file(store.profile(account)/'auth.json'))
                if (candidate.get('tokens', {}).get('account_id') == identity
                        and (hub.auth_email(candidate) or '').casefold() == email.casefold()):
                    matches.append(account['id'])
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        return matches[0] if len(matches) == 1 else None
    except (ImportError, OSError, ValueError, AttributeError, TypeError, KeyError):
        return None


def pool_activity(store):
    """Only fresh, live per-pool request counts qualify as current activity."""
    try:
        status = json.loads(hub.read_file(store.root/'pool-status.json'))
        if not 0 <= time.time() - status['updated_at'] < 10:
            return {}
        pid = status['pid']
        if type(pid) is not int or pid <= 0:
            return {}
        os.kill(pid, 0)
        result = {}
        for index, counts in status.get('inflight_by_pool', {}).items():
            if not isinstance(counts, dict):
                continue
            for identity, count in counts.items():
                if type(count) is int and count > 0:
                    result.setdefault(identity, []).append(f'current pool {int(index)+1}')
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def switch(store, pool=None, account=None):
    try:
        import tomlkit
    except ImportError:
        raise ValueError('Install switching support: python3 -m pip install --user -r linux/requirements-pools.txt') from None
    home = hub.ordinary(Path(store.settings['default_home']))
    config_path, auth_path = home/'config.toml', home/'auth.json'
    new_auth = hub.read_file(store.validate(account)/'auth.json') if account else None
    if new_auth is not None:
        hub.Store.check_auth(new_auth)
    with (home/'.hub-switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = hub.read_file(config_path) if config_path.exists() else None
        original_auth = hub.read_file(auth_path) if auth_path.exists() else None
        doc = tomlkit.parse((original or b'').decode('utf-8-sig'))
        doc['cli_auth_credentials_store'] = 'file'
        if pool:
            provider_id = 'codex_hub_'+pool['id'].replace('-', '_')
            providers = doc.setdefault('model_providers', tomlkit.table())
            existing = providers.get(provider_id)
            if existing and existing.get('name') != 'Codex CLI Hub '+pool['id']:
                raise ValueError('Provider name conflict in Codex config; no changes made.')
            provider = tomlkit.table()
            provider.update(name='Codex CLI Hub '+pool['id'], base_url=f'http://127.0.0.1:{pool["port"]}/v1',
                            wire_api='responses', requires_openai_auth=False, supports_websockets=True,
                            experimental_bearer_token=pool['key'])
            providers[provider_id] = provider
            doc['model_provider'] = provider_id
        else:
            doc['model_provider'] = 'openai'
        active = doc.get('profile')
        if active:
            profiles = doc.get('profiles', {})
            if not isinstance(active, str) or active not in profiles:
                raise ValueError('Default Codex profile is missing; fix it in config before switching.')
            profiles[active]['model_provider'] = doc['model_provider']
        updated = tomlkit.dumps(doc).encode()
        backups = hub.ordinary(store.root/'switch-backups')
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='switch-', dir=backups))
        for name, content in [('config.toml', original), ('auth.json', original_auth)]:
            if content is not None:
                hub.atomic_write(backup/name, content)
        hub.atomic_write(backup/'manifest.json', json.dumps({'config_existed': original is not None, 'auth_existed': original_auth is not None}).encode())
        if (hub.read_file(config_path) if config_path.exists() else None) != original:
            raise ValueError('Codex config changed during switching; retry after closing other clients.')
        auth_written = False
        try:
            if new_auth is not None:
                if (hub.read_file(auth_path) if auth_path.exists() else None) != original_auth:
                    raise ValueError('Default login changed during switching; retry.')
                hub.atomic_write(auth_path, new_auth)
                auth_written = True
            hub.atomic_write(config_path, updated)
        except Exception:
            if auth_written and hub.read_file(auth_path) == new_auth:
                if original_auth is None:
                    auth_path.unlink()
                else:
                    hub.atomic_write(auth_path, original_auth)
            raise
        return backup


def switch_pool(store, pool):
    return switch(store, pool=pool)


def switch_account(store, account):
    return switch(store, account=account)
