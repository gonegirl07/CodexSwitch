"""Linux profile storage and Codex integration. Python 3.10+, standard library only."""
import fcntl
import base64
import binascii
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ISOLATED_KEYS = {
    'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_ACCESS_TOKEN',
    'CODEX_THREAD_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CODEX_REMOTE',
    'CODEX_REMOTE_AUTH_TOKEN', 'CODEX_APP_SERVER_URL', 'CODEX_APP_SERVER_AUTH_TOKEN',
    'CODEX_SESSION_ID', 'CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_CI',
    'CODEX_PERMISSION_PROFILE', 'CODEX_SHELL', 'CODEX_MCP_NODE_PATH',
    'CODEX_SAGE_BACKFILL_TRACKER_TAB_REUSE',
}
CREDENTIAL_ARGS = ['-c', 'cli_auth_credentials_store="file"']


def jwt_payload(value):
    """Read display metadata only; token validation remains the OAuth service's job."""
    try:
        encoded = value.split('.')[1]
        result = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        return result if isinstance(result, dict) else {}
    except (AttributeError, IndexError, ValueError, binascii.Error):
        return {}


def account_priority_value(account):
    value = account.get('priority', 1) if isinstance(account, dict) else 1
    return value if type(value) is int and 0 <= value <= 4 else 1


def _http_status(value):
    if type(value) is int and 400 <= value <= 599:
        return value
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        if 400 <= number <= 599:
            return number
    return None


def _error_code(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,40}', value):
        return value
    return None


def error_detail(source):
    http, code, text = None, None, ''
    seen = set()

    def walk(value):
        nonlocal http, code, text
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        if value is None:
            return
        if isinstance(value, dict):
            http = http or _http_status(value.get('http_status') or value.get('status_code') or value.get('http'))
            for key in ('error_code', 'code'):
                parsed = _error_code(value.get(key))
                if parsed:
                    code = code or parsed
            walk(value.get('data'))
            for key in ('quota_error', 'message', 'error', 'status'):
                item = value.get(key)
                if isinstance(item, str) and item and not text:
                    text = item
                walk(item)
            return
        blob = value if isinstance(value, str) else str(value)
        if not text:
            text = blob
        stripped = blob.strip()
        if stripped[:1] in '{[':
            try:
                walk(json.loads(stripped))
            except (ValueError, TypeError):
                pass
        if not http:
            match = re.search(r'(?:status\s*=\s*)?(?<![.\w])([45]\d{2})(?!\d)', blob)
            if match:
                http = int(match[1])
        if not code:
            match = re.search(r'error_code\s*=\s*Some\("([A-Za-z0-9_]+)"\)', blob)
            if match:
                code = match[1]
            else:
                match = re.search(r'error_code\s*[=:]\s*([A-Za-z][A-Za-z0-9_]{2,40})', blob)
                if match:
                    code = match[1]
                else:
                    lower = blob.casefold()
                    for name in ('refresh_token_reused', 'token_invalidated', 'invalid_grant',
                                 'login_required', 'unauthorized', 'forbidden'):
                        if name in lower:
                            code = name
                            break
                    if 'log in before reading quota' in lower or 'not logged in' in lower or 'invalid auth.json' in lower:
                        code = code or 'login_required'
        if not http and (code or '').casefold() in ('unauthorized', 'token_invalidated', 'refresh_token_reused',
                                                    'invalid_grant', 'login_required'):
            http = 401
        if not http and (code or '').casefold() == 'forbidden':
            http = 403

    walk(source)
    return {'http': http, 'code': code, 'text': text}


def format_error_label(source):
    detail = error_detail(source)
    parts = []
    if detail['http']:
        parts.append(str(detail['http']))
    if detail['code'] and str(detail['code']) not in parts:
        parts.append(str(detail['code']))
    return ' · '.join(parts)


def login_required(update):
    status = str((update or {}).get('status') or '')
    error = str((update or {}).get('quota_error') or '')
    detail = error_detail(update)
    if detail.get('http') == 403 or str(detail.get('code') or '').casefold() == 'forbidden':
        return False
    if detail.get('http') == 401:
        return True
    if str(detail.get('code') or '').casefold() in (
            'token_invalidated', 'refresh_token_reused', 'login_required', 'unauthorized', 'invalid_grant'):
        return True
    return 'Not logged in' in status or 'Log in before reading quota' in error or 'Invalid auth.json' in error


def auth_email(auth):
    tokens = auth.get('tokens') if isinstance(auth, dict) else None
    if not isinstance(tokens, dict):
        return None
    for field in ('id_token', 'access_token'):
        payload = jwt_payload(tokens.get(field, ''))
        profile = payload.get('https://api.openai.com/profile')
        email = payload.get('email') or (profile.get('email') if isinstance(profile, dict) else None)
        if isinstance(email, str) and len(email) <= 254 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            return email
    return None


def subscription_name(value):
    # Explicit provider identifiers only; quota size is not a subscription tier.
    names = {'free': 'Free', 'plus': 'Plus', 'pro': 'Pro', 'pro_5x': 'Pro5x',
             'pro_20x': 'Pro20x', 'team': 'Business', 'business': 'Business',
             'enterprise': 'Enterprise', 'edu': 'Edu', 'go': 'Go', 'prolite': 'Pro Lite',
             'self_serve_business_prolite': 'Business', 'self_serve_business_usage_based': 'Business',
             'ent26': 'Enterprise', 'enterprise_cbp_automation': 'Enterprise',
             'enterprise_cbp_usage_based': 'Enterprise', 'edu_plus': 'Edu Plus', 'edu_pro': 'Edu Pro'}
    return names.get(value.strip().lower()) if isinstance(value, str) else None


def auth_subscription(auth):
    tokens = auth.get('tokens', {})
    if not isinstance(tokens, dict):
        return None
    for field in ('access_token', 'id_token'):
        claims = jwt_payload(tokens.get(field, '')).get('https://api.openai.com/auth')
        if isinstance(claims, dict):
            tier = subscription_name(claims.get('chatgpt_plan_type'))
            if tier:
                return tier
    return None


def subscription_timestamp(value):
    try:
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    return None
                value = parsed.timestamp()
        if type(value) in (int, float) and math.isfinite(value) and 0 < value < 253402300800:
            return value
    except (ValueError, OverflowError, OSError):
        pass
    return None


def subscription_remaining(account, now=None):
    expiry = subscription_timestamp(account.get('subscription_active_until'))
    if expiry is None:
        return '--'
    remaining = expiry - (time.time() if now is None else now)
    if remaining <= 0:
        return 'Expired'
    if remaining >= 86400:
        return f'{int(remaining // 86400)}d'
    return f'{int(remaining // 3600)}h' if remaining >= 3600 else '<1h'


def ordinary(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'Symlink is not allowed here: {part}')
    return path


def atomic_write(path, data):
    path = ordinary(path)
    fd, temporary = tempfile.mkstemp(prefix='.hub-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        ordinary(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_file(path, limit=-1):
    path = ordinary(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'Expected a regular file: {path}')
        return stream.read(limit)


def file_config(text):
    """Lexically replace only the root key, retaining the rest of the TOML verbatim."""
    assignment = re.compile(r'''(?:cli_auth_credentials_store|"cli_auth_credentials_store"|'cli_auth_credentials_store')[ \t]*=[ \t]*''')

    def string_end(start):
        quote = text[start]
        triple = text.startswith(quote * 3, start)
        i = start + (3 if triple else 1)
        while i < len(text):
            if quote == '"' and text[i] == '\\':
                i += 2
                continue
            if text.startswith(quote * (3 if triple else 1), i):
                end = i + (3 if triple else 1)
                if triple:
                    while end < min(len(text), i + 5) and text[end] == quote:
                        end += 1
                return end
            i += 1
        raise ValueError('Unterminated string in config.toml; no changes made.')

    i, depth, line_start, replacement = 0, 0, True, None
    while i < len(text):
        c = text[i]
        if c == '\n':
            line_start = True
            i += 1
            continue
        if c in ' \t\r\ufeff':
            i += 1
            continue
        if c == '#':
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        if line_start and depth == 0:
            if c == '[':
                break
            match = assignment.match(text, i)
            if match:
                if replacement is not None:
                    raise ValueError('Duplicate credential store keys in config.toml.')
                start = match.end()
                if start >= len(text) or text[start] not in "\"'":
                    raise ValueError('Credential store must be a string.')
                i = string_end(start)
                replacement = (start, i)
                line_start = False
                continue
        line_start = False
        if c in "\"'":
            i = string_end(i)
            continue
        if c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
        i += 1
    if replacement:
        start, end = replacement
        return text[:start] + '"file"' + text[end:]
    ending = '\r\n' if '\r\n' in text else '\n'
    bom = '\ufeff' if text.startswith('\ufeff') else ''
    return bom + 'cli_auth_credentials_store = "file"' + ending + text[len(bom):]


class Store:
    def __init__(self, root):
        self.root = ordinary(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        lock = ordinary(self.root / 'manager.lock')
        self.lock = os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            raise RuntimeError('Another manager is already using this data directory.') from None
        try:
            self.settings = self.load('settings.json', {
                'version': 1, 'default_home': str(Path.home() / '.codex'),
                'working_directory': str(Path.home()), 'codex': 'codex',
            })
            for key in ('default_home', 'working_directory', 'codex'):
                if not isinstance(self.settings.get(key), str) or not self.settings[key]:
                    raise ValueError(f'Invalid settings field: {key}')
            document = self.load('accounts.json', {'version': 1, 'accounts': []})
            self.accounts = document.get('accounts')
            if not isinstance(self.accounts, list):
                raise ValueError('Invalid accounts document.')
            seen = set()
            for account in self.accounts:
                if not isinstance(account, dict):
                    raise ValueError('Invalid account.')
                self.profile(account)
                self.check_name(account.get('name'), account.get('note'))
                if account['id'] in seen:
                    raise ValueError('Duplicate account ID.')
                seen.add(account['id'])
            ordinary(self.root / 'profiles').mkdir(exist_ok=True, mode=0o700)
        except Exception:
            self.close()
            raise

    def close(self):
        self.lock.close()

    def load(self, name, default):
        path = ordinary(self.root / name)
        if not path.exists():
            return default
        try:
            result = json.loads(read_file(path))
        except (UnicodeError, ValueError):
            raise ValueError(f'Invalid {name}; existing data was not reset.') from None
        if not isinstance(result, dict) or type(result.get('version')) is not int or result['version'] != 1:
            raise ValueError(f'Unsupported {name} version; existing data was not reset.')
        return result

    def save_accounts(self):
        atomic_write(self.root / 'accounts.json', json.dumps({'version': 1, 'accounts': self.accounts}, ensure_ascii=False, indent=2).encode())

    def save_settings(self, settings):
        home = ordinary(settings['default_home'])
        work = ordinary(settings['working_directory'])
        if not home.is_dir() or not work.is_dir():
            raise ValueError('Default Codex home and working directory must exist.')
        if self.accounts and home != ordinary(self.settings['default_home']):
            raise ValueError('Remove accounts before changing the shared Codex home.')
        settings = dict(settings, default_home=str(home), working_directory=str(work))
        atomic_write(self.root / 'settings.json', json.dumps(settings, indent=2).encode())
        self.settings = settings

    @staticmethod
    def check_name(name, note):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 254 or not isinstance(note, str) or len(note) > 1000:
            raise ValueError('Account label: 1–254 characters. Note: at most 1000 characters.')

    def profile(self, account):
        if not re.fullmatch(r'[0-9a-f]{32}', str(account.get('id', ''))):
            raise ValueError('Invalid profile ID.')
        return ordinary(self.root / 'profiles' / account['id'])

    def shared_target(self):
        target = ordinary(Path(self.settings['default_home']) / 'sessions')
        if target == self.root or target in self.root.parents or self.root in target.parents:
            raise ValueError('Shared sessions and manager data directories must not overlap.')
        if not target.is_dir():
            raise ValueError('Default Codex sessions directory is missing. Run Codex once or choose an existing Codex home in Settings.')
        return target

    def validate(self, account):
        profile = self.profile(account)
        if not profile.is_dir():
            raise ValueError('Profile directory is missing.')
        link = profile / 'sessions'
        if not link.is_symlink() or link.resolve() != self.shared_target():
            raise ValueError('Shared sessions link was changed or is missing; operation refused.')
        ordinary(profile / 'auth.json')
        ordinary(profile / 'config.toml')
        return profile

    def ensure_config(self, profile):
        path = ordinary(profile / 'config.toml')
        original = read_file(path).decode('utf-8') if path.exists() else ''
        updated = file_config(original)
        if original != updated:
            atomic_write(path, updated.encode('utf-8'))

    def create(self, name, note, copy_auth=False, auth_data=None):
        self.check_name(name, note)
        target = self.shared_target()
        home = ordinary(self.settings['default_home'])
        config_path = ordinary(home / 'config.toml')
        config = read_file(config_path).decode('utf-8') if config_path.exists() else ''
        config = file_config(config)
        auth = read_file(home / 'auth.json') if copy_auth else auth_data
        if auth is not None:
            self.check_auth(auth)
        account = {'id': uuid.uuid4().hex, 'name': name.strip(), 'note': note.strip(), 'status': 'Not checked'}
        profile = self.profile(account)
        profile.mkdir(mode=0o700)
        try:
            atomic_write(profile / 'config.toml', config.encode())
            if auth is not None:
                atomic_write(profile / 'auth.json', auth)
            (profile / 'sessions').symlink_to(target, target_is_directory=True)
            self.sync_identity(account)
            self.accounts.append(account)
            try:
                self.save_accounts()
            except Exception:
                self.accounts.remove(account)
                raise
        except Exception:
            shutil.rmtree(profile)
            raise
        return account

    def identity_email(self, account):
        for candidate in (account.get('email'), account.get('name')):
            if isinstance(candidate, str) and '@' in candidate and candidate not in ('Pending login', 'Email unavailable'):
                return candidate
        saved = self.load_login(account)
        if saved and isinstance(saved.get('email'), str) and saved['email']:
            return saved['email']
        return None

    def sync_identity(self, account):
        fallback = self.identity_email(account)
        path = self.profile(account) / 'auth.json'
        if not path.exists():
            account.update(name=fallback or 'Pending login', email=fallback)
            return
        auth = json.loads(read_file(path))
        if not isinstance(auth, dict):
            raise ValueError('Invalid auth.json; cannot read account email.')
        email = auth_email(auth) or fallback
        account.update(email=email, name=email or ('API key account' if auth.get('OPENAI_API_KEY') else fallback or 'Email unavailable'))
        tier = auth_subscription(auth)
        # Unchanged token metadata must not undo a newer quota response tier.
        if tier and (tier != account.get('auth_subscription') or not account.get('subscription')):
            account.update(subscription=tier, auth_subscription=tier)
        tokens = auth.get('tokens', {})
        if isinstance(tokens, dict):
            for field in ('access_token', 'id_token'):
                claims = jwt_payload(tokens.get(field, '')).get('https://api.openai.com/auth')
                expiry = subscription_timestamp(claims.get('chatgpt_subscription_active_until')) if isinstance(claims, dict) else None
                if expiry is not None:
                    account['subscription_active_until'] = expiry
                    break

    def import_auth(self, auth, expected_email):
        email = auth_email(auth)
        if not email or email.casefold() != expected_email.casefold():
            raise ValueError('Logged-in email does not match the requested account.')
        tokens = auth.get('tokens', {})
        if any(not isinstance(tokens.get(key), str) or not tokens[key] for key in ('access_token', 'refresh_token', 'id_token', 'account_id')):
            raise ValueError('Incomplete OAuth credentials; account was not added.')
        if any((account.get('email') or account['name']).casefold() == email.casefold() for account in self.accounts):
            raise ValueError('This email is already in the manager; existing login was preserved.')
        return self.create(email, '', auth_data=json.dumps(auth).encode())

    @staticmethod
    def check_auth(data):
        try:
            valid = isinstance(json.loads(data), dict)
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise ValueError('Invalid auth.json; default login was not changed.')

    def apply(self, account):
        profile = self.validate(account)
        data = read_file(profile / 'auth.json')
        self.check_auth(data)
        atomic_write(Path(self.settings['default_home']) / 'auth.json', data)

    def load_login(self, account):
        path = ordinary(self.profile(account) / 'login.json')
        if not path.exists():
            return None
        try:
            data = json.loads(read_file(path))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(data, dict):
            return None
        email, password, totp = data.get('email'), data.get('password'), data.get('totp') or ''
        if not isinstance(email, str) or not isinstance(password, str) or not password or not isinstance(totp, str):
            return None
        return {'email': email, 'password': password, 'totp': totp}

    def save_login(self, account, email, password, totp=''):
        import bulk
        parsed = bulk.parse_accounts(f'{email}|{password}|{totp}' if totp else f'{email}|{password}')
        email, password, totp = parsed[0]
        path = ordinary(self.profile(account) / 'login.json')
        atomic_write(path, json.dumps({'email': email, 'password': password, 'totp': totp}).encode())
        os.chmod(path, 0o600)

    def clear_login(self, account):
        path = ordinary(self.profile(account) / 'login.json')
        if path.exists():
            path.unlink()

    def replace_auth(self, account, auth):
        payload = json.dumps(auth).encode()
        self.check_auth(payload)
        email = auth_email(auth)
        expected = account.get('email')
        if expected and email and email.casefold() != expected.casefold():
            raise ValueError('Logged-in email does not match the requested account.')
        if not email:
            raise ValueError('OAuth response is missing the account email.')
        atomic_write(self.profile(account) / 'auth.json', payload)
        self.sync_identity(account)
        self.save_accounts()

    def delete(self, account):
        profile = self.validate(account)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError('Safe directory deletion is unavailable on this Python build.')
        # fd-based rmtree unlinks symlinks, including sessions, without traversing targets.
        shutil.rmtree(profile)
        self.accounts.remove(account)
        self.save_accounts()


def isolated_env(profile, source=None):
    env = dict(os.environ if source is None else source)
    for key in ISOLATED_KEYS:
        env.pop(key, None)
    env['CODEX_HOME'] = str(profile)
    return env


def codex_path(settings):
    configured = os.path.expanduser(settings['codex'])
    executable = shutil.which(configured)
    # Desktop launchers do not inherit interactive shell PATH additions.
    # Only auto-discover the default name; respect explicit custom settings.
    if not executable and configured == 'codex':
        for relative in ('.local/bin/codex', '.npm-global/bin/codex', '.codex/packages/standalone/current/bin/codex'):
            executable = shutil.which(str(Path.home() / relative))
            if executable:
                break
    if not executable:
        raise ValueError('Codex CLI not found. Set its executable path in Settings.')
    return executable


def projects(sessions):
    paths, skipped = set(), 0
    for directory, dirs, files in os.walk(sessions, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.suffix != '.jsonl' or path.is_symlink():
                continue
            found = False
            try:
                with path.open('rb') as stream:
                    header = stream.read(128 * 1024)
                for line in header.splitlines():
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(item, dict) and item.get('type') == 'session_meta':
                        payload = item.get('payload')
                        cwd = payload.get('cwd') if isinstance(payload, dict) else None
                        if isinstance(cwd, str) and cwd.startswith('/') and '\0' not in cwd:
                            paths.add(os.path.normpath(cwd))
                            found = True
                        break
            except OSError:
                pass
            skipped += not found
    return sorted(paths, key=lambda p: (not Path(p).is_dir(), Path(p).name.casefold(), p)), skipped


def quota_window_label(minutes):
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
        return 'Unknown'
    if minutes == 10080:
        return 'Weekly'
    for divisor, suffix in ((1440, 'd'), (60, 'h'), (1, 'm')):
        if minutes % divisor == 0:
            return f'{minutes / divisor:g}{suffix}'
    return f'{minutes:g}m'


def quota_windows(result):
    """Main Codex windows only; primary/secondary are identities, not durations."""
    buckets = result.get('rateLimitsByLimitId')
    bucket = buckets.get('codex') if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict):
        bucket = result.get('rateLimits')
    if not isinstance(bucket, dict):
        return []
    windows = []
    for field in ('primary', 'secondary'):
        window = bucket.get(field)
        if window is None:
            continue
        window = window if isinstance(window, dict) else {}
        used = window.get('usedPercent')
        remaining = max(0, min(100, 100-used)) if type(used) in (int, float) and math.isfinite(used) else None
        minutes = window.get('windowDurationMins')
        windows.append({'id': field, 'label': quota_window_label(minutes), 'minutes': minutes,
                        'remaining': remaining, 'resets_at': window.get('resetsAt')})
    return windows


def account_quota_windows(account):
    if isinstance(account.get('quota_windows'), list):
        return account['quota_windows']
    # Version-1 accounts stored readable lines only. Keep them usable until refresh.
    windows = []
    for line in account.get('quota', []):
        match = re.match(r'^codex · (.+): (\d+(?:\.\d+)?% left|No data)', line)
        if not match:
            continue
        period, value = match.groups()
        if period in ('Spending', 'Credits', 'Limit reached'):
            continue
        duration = re.fullmatch(r'(\d+(?:\.\d+)?) minutes', period)
        minutes = float(duration[1]) if duration else {'5 hours': 300, 'Weekly': 10080}.get(period)
        label = quota_window_label(minutes) if minutes is not None else period if re.fullmatch(r'\d+[dhm]', period) else 'Unknown'
        windows.append({'id': f'legacy-{len(windows)}', 'label': label, 'minutes': minutes,
                        'remaining': float(value.split('%')[0]) if value != 'No data' else None, 'resets_at': None})
    return windows


def quota_remaining(lines, period, bucket='codex'):
    for line in lines:
        match = re.match(r'^' + re.escape(bucket + ' · ' + period) + r': (\d+(?:\.\d+)?)% left', line, re.IGNORECASE)
        if match:
            return max(0, min(100, float(match[1])))
    return None


def reset_count(lines):
    for line in lines:
        match = re.fullmatch(r'Reset count: (\d+)', line)
        if match:
            return int(match[1])
    return None


def quota_lines(result):
    lines, seen = [], set()
    buckets = result.get('rateLimitsByLimitId') or {}
    buckets = list(buckets.items()) if isinstance(buckets, dict) else []
    buckets.append(('codex', result.get('rateLimits')))
    for key, bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        identity = bucket.get('limitId') or key
        if identity in seen:
            continue
        seen.add(identity)
        label = bucket.get('limitName') or identity
        if identity == 'codex':
            label = 'codex'
        elif identity in ('base_model_inference', 'gpt-reserve'):
            label = 'gpt-reserve'
        for field, window in bucket.items():
            if not isinstance(window, dict) or 'usedPercent' not in window:
                continue
            used = window['usedPercent']
            value = f'{max(0, min(100, 100 - used)):g}% left' if type(used) in (int, float) and math.isfinite(used) else 'No data'
            minutes = window.get('windowDurationMins')
            period = {300: '5 hours', 10080: 'Weekly'}.get(minutes, quota_window_label(minutes))
            reset = ''
            if type(window.get('resetsAt')) is int:
                try:
                    reset = time.strftime(' · reset %d/%m %H:%M', time.localtime(window['resetsAt']))
                except (ValueError, OverflowError, OSError):
                    reset = ' · reset unknown'
            lines.append(f'{label} · {period}: {value}{reset}')
        credits = bucket.get('credits')
        if isinstance(credits, dict):
            detail = 'Unlimited' if credits.get('unlimited') else credits.get('balance') or ('No credits' if credits.get('hasCredits') is False else 'Balance unavailable')
            lines.append(f'{label} · Credits: {detail}')
        limit = bucket.get('individualLimit')
        if isinstance(limit, dict):
            lines.append(f"{label} · Spending: {limit.get('remainingPercent', '?')}% left · {limit.get('used', '?')} / {limit.get('limit', '?')}")
        if bucket.get('rateLimitReachedType'):
            lines.append(f"{label} · Limit reached: {bucket['rateLimitReachedType']}")
        if bucket.get('spendControlReached'):
            lines.append(f'{label} · Spending limit reached')
    resets = result.get('rateLimitResetCredits')
    if isinstance(resets, dict) and 'availableCount' in resets:
        lines.append(f"Reset count: {resets['availableCount']}")
    return lines


def read_quota(executable, profile, timeout=30, metadata=None, reset_key=None):
    process = subprocess.Popen([executable, *CREDENTIAL_ARGS, 'app-server'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=isolated_env(profile), cwd=profile, start_new_session=True)
    deadline, buffer = time.monotonic() + timeout, b''
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def send(message):
        process.stdin.write(json.dumps(message).encode() + b'\n')
        process.stdin.flush()

    def receive(request_id):
        nonlocal buffer
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('Quota request timed out; try Refresh again.')
            if b'\n' not in buffer:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError('Quota request timed out; try Refresh again.')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError('Codex closed the quota connection.')
                buffer += chunk
                if len(buffer) > 4 * 1024 * 1024:
                    raise ValueError('Quota response exceeds the size limit.')
                continue
            line, buffer = buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
            except ValueError:
                raise ValueError('Invalid quota response.') from None
            if not isinstance(message, dict) or message.get('id') != request_id:
                continue
            if 'error' in message:
                err = message['error']
                label = format_error_label(err)
                text = label or (err.get('message') if isinstance(err, dict) else None)
                raise ValueError(text or 'Quota unavailable. Check login and network, then Refresh.')
            if not isinstance(message.get('result'), dict):
                raise ValueError('Invalid quota result.')
            return message['result']
    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'codex_cli_hub_linux', 'version': '1.0.0'}, 'capabilities': {'experimentalApi': True}}})
        receive(1)
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'account/rateLimits/read'})
        result = receive(2)
        if reset_key is not None:
            count = reset_count(quota_lines(result))
            if count is None:
                raise ValueError('Reset count unavailable. Refresh before resetting.')
            outcome = 'noCredit'
            if count > 0:
                send({'id': 3, 'method': 'account/rateLimitResetCredit/consume',
                      'params': {'idempotencyKey': reset_key}})
                outcome = receive(3).get('outcome')
                if outcome not in ('reset', 'nothingToReset', 'noCredit', 'alreadyRedeemed'):
                    raise ValueError('Reset outcome unknown. Refresh before retrying.')
            if metadata is not None:
                metadata['reset_outcome'] = outcome
            if count > 0:
                send({'id': 4, 'method': 'account/rateLimits/read'})
                result = receive(4)
        if metadata is not None:
            metadata['quota_windows'] = quota_windows(result)
            bucket = result.get('rateLimits')
            tier = subscription_name(bucket.get('planType')) if isinstance(bucket, dict) else None
            if tier:
                metadata['subscription'] = tier
        return quota_lines(result)
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        process.stdout.close()


def profile_quota(executable, profile, metadata=None, reset_key=None):
    """Query official account limits without starting session DB backfill in the profile."""
    profile = ordinary(profile)
    auth_path = profile / 'auth.json'
    original = read_file(auth_path)
    Store.check_auth(original)
    with tempfile.TemporaryDirectory(prefix='.quota-', dir=profile) as temporary:
        home = Path(temporary)
        atomic_write(home / 'auth.json', original)
        # Quota needs authentication only. Profile config can redirect sqlite_home
        # or load unrelated integrations, so use a minimal config for this request.
        atomic_write(home / 'config.toml', b'cli_auth_credentials_store = "file"\n')
        try:
            if reset_key is not None:
                lines = read_quota(executable, home, metadata=metadata, reset_key=reset_key)
            else:
                lines = read_quota(executable, home) if metadata is None else read_quota(executable, home, metadata=metadata)
            if not lines:
                raise ValueError('Codex returned no quota windows for this account.')
            return lines
        finally:
            updated = read_file(home / 'auth.json')
            if updated != original:
                Store.check_auth(updated)
                # Preserve token rotation, but never replace a newer login from a terminal.
                if read_file(auth_path) == original:
                    atomic_write(auth_path, updated)


def reset_quota(executable, profile, key):
    metadata = {}
    try:
        lines = profile_quota(executable, profile, metadata, reset_key=key)
        return {'quota': lines, 'quota_error': None, 'checked_at': time.time(), **metadata}
    except (OSError, ValueError, TimeoutError) as error:
        return {'quota_error': 'Reset could not be confirmed / quota unavailable. Refresh before retrying. ' + str(error), **metadata}


def refresh(executable, profile):
    result = subprocess.run([executable, *CREDENTIAL_ARGS, 'login', 'status'],
                            env=isolated_env(profile), cwd=profile, capture_output=True, timeout=20)
    output = (result.stdout + result.stderr).decode(errors='replace').lower()
    status = 'Logged in (local credentials)' if result.returncode == 0 and 'logged in' in output else 'Not logged in' if 'not logged in' in output else f'Check failed (exit {result.returncode})'
    if status != 'Logged in (local credentials)':
        return {'status': status, 'quota_error': 'Log in before reading quota.'}
    try:
        metadata = {}
        lines = profile_quota(executable, profile, metadata)
        return {'status': status, 'quota': lines, 'quota_error': None, 'checked_at': time.time(), **metadata}
    except (OSError, ValueError, TimeoutError) as error:
        return {'status': status, 'quota_error': str(error)}


def open_path(path):
    opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
    return subprocess.Popen([opener, str(path)])


def launch(executable, profile, working_directory, action):
    work = Path(working_directory).expanduser().absolute()
    if not work.is_dir():
        raise ValueError('Choose an existing project directory.')
    helper = [sys.executable, str(Path(__file__).resolve()), '--terminal', executable, str(profile), str(work), action]
    if sys.platform == 'darwin':
        script = tempfile.NamedTemporaryFile('w', suffix='.command', delete=False)
        script.write('#!/bin/sh\nexec ' + ' '.join(shlex.quote(part) for part in helper) + '\n')
        script.close()
        os.chmod(script.name, 0o700)
        return subprocess.Popen(['open', '-a', 'Terminal', script.name], cwd=work, start_new_session=True)
    # Set CODEX_HOME inside the terminal: GNOME Terminal's D-Bus server may reuse its own environment.
    for terminal, flags in [('gnome-terminal', ['--wait', '--']), ('konsole', ['--separate', '-e']), ('xfce4-terminal', ['--disable-server', '-x']), ('xterm', ['-e'])]:
        path = shutil.which(terminal)
        if path:
            return subprocess.Popen([path, *flags, *helper], cwd=work, start_new_session=True)
    raise ValueError('Install gnome-terminal, konsole, xfce4-terminal or xterm to open Codex.')


def terminal_main(executable, profile, work, action):
    os.umask(0o077)
    env = isolated_env(profile)
    args = {'open': [], 'login': ['login'], 'resume': ['resume', '--all']}[action]
    try:
        result = subprocess.run([executable, *CREDENTIAL_ARGS, *args], cwd=work, env=env)
        code = result.returncode
    except OSError as error:
        print(f'Cannot start Codex: {error}')
        code = 1
    if action != 'login' or code != 0:
        try:
            input(f'\nCodex exited ({code}). Press Enter to close this terminal…')
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == '__main__' and len(sys.argv) == 6 and sys.argv[1] == '--terminal':
    sys.exit(terminal_main(*sys.argv[2:]))
