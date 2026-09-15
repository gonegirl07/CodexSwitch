"""Codex auth.json import and interactive browser OAuth. No secret logging."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import threading
import time

import bulk
import hub
import pools

MAX_JSON = 2 * 1024 * 1024


def parse_auth_json(text):
    if len(text.encode('utf-8')) > MAX_JSON:
        raise ValueError('JSON is too large (maximum 2 MiB).')
    try:
        document = json.loads(text.lstrip('\ufeff'))
    except (ValueError, RecursionError):
        raise ValueError('Invalid JSON. Select a Codex auth.json file or paste its full contents.') from None
    documents = document if isinstance(document, list) else [document]
    if not 1 <= len(documents) <= 100:
        raise ValueError('Import 1–100 Codex auth.json objects at a time.')
    for number, auth in enumerate(documents, 1):
        error = f'Item {number}: requires ChatGPT auth.json with access_token, refresh_token, id_token, account_id and an email.'
        if not isinstance(auth, dict) or auth.get('auth_mode', 'chatgpt') != 'chatgpt' or auth.get('OPENAI_API_KEY'):
            raise ValueError(error + ' API-key-only credentials cannot be used for account pools.')
        tokens = auth.get('tokens')
        if not isinstance(tokens, dict) or any(not isinstance(tokens.get(k), str) or not tokens[k].strip()
                for k in ('access_token', 'refresh_token', 'id_token', 'account_id')) or not hub.auth_email(auth):
            raise ValueError(error)
        identity = hub.jwt_payload(tokens['access_token']).get('https://api.openai.com/auth')
        if isinstance(identity, dict) and identity.get('chatgpt_account_id') and identity['chatgpt_account_id'] != tokens['account_id']:
            raise ValueError(f'Item {number}: account ID does not match the access token.')
        emails = {hub.auth_email({'tokens': {key: tokens[key]}}) for key in ('access_token', 'id_token')}
        if len(emails - {None}) > 1:
            raise ValueError(f'Item {number}: token emails do not match.')
    return documents


def import_accounts(store, documents, selected_pools, note):
    documents = parse_auth_json(json.dumps(documents))
    store.check_name('Imported account', note)
    config = pools.load_config(store.root)
    if any(type(index) is not int or index < 0 or index >= len(config['pools']) for index in selected_pools):
        raise ValueError('Select an existing pool, or neither.')
    added, skipped = [], []
    warning = ''
    for auth in documents:
        email = hub.auth_email(auth)
        if any((a.get('email') or a['name']).casefold() == email.casefold() for a in store.accounts):
            skipped.append(email+' · already added; unchanged')
            continue
        try:
            account = store.import_auth(auth, email)
        except (OSError, ValueError):
            warning = 'Import stopped after a local save error. Accounts listed as added were preserved; retry the remaining files.'
            break
        added.append(account)
        for index in selected_pools:
            config['pools'][index]['members'].append(account['id'])
        previous_note = account['note']
        account['note'] = note.strip()
        try:
            store.save_accounts()
        except (OSError, ValueError):
            account['note'] = previous_note
            warning = 'Import stopped: account saved but note could not be saved. Retry the remaining files.'
            break
    if added and selected_pools:
        try:
            pools.save_config(store.root, config)
        except (OSError, ValueError):
            warning += ' Accounts saved, but pool assignment failed. Assign them manually in Pools.'
    return added, skipped, warning


class OAuthFlow:
    def __init__(self):
        self.url, self.verifier, self.state = bulk.authorization(
            originator='codex_vscode',
            scope='openid profile email offline_access api.connectors.read api.connectors.invoke')
        self.deadline = time.monotonic() + 600
        self.cancel = threading.Event()
        self.codes = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.used = False
        self.server = self.thread = None

    def submit(self, callback):
        if len(callback) > 8192:
            raise ValueError('Callback URL is too long.')
        code = bulk.callback_code(callback.strip(), self.state)
        with self.lock:
            if self.used or self.cancel.is_set() or time.monotonic() >= self.deadline:
                raise ValueError('OAuth session expired, was cancelled, or callback was already received.')
            self.used = True
            self.codes.put_nowait(code)

    def listen(self, port=1455):
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Request URLs contain a one-time authorization code.

            def do_GET(self):
                try:
                    if self.headers.get('Host') != 'localhost:1455':
                        raise ValueError('Unexpected callback host.')
                    flow.submit('http://localhost:1455'+self.path)
                    status, body = 200, b'Authorization received. Return to Codex CLI Hub to finish.'
                except ValueError:
                    status, body = 400, b'Invalid, expired or already received callback. Return to Hub.'
                self.send_response(status)
                for name, value in [('Content-Type', 'text/plain; charset=utf-8'), ('Cache-Control', 'no-store'),
                                    ('Content-Length', str(len(body))), ('Content-Security-Policy', "default-src 'none'")]:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        try:
            self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        except OSError:
            return False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()
        return True

    def finish(self):
        try:
            while not self.cancel.is_set() and time.monotonic() < self.deadline:
                try:
                    code = self.codes.get(timeout=0.1)
                    break
                except queue.Empty:
                    continue
            else:
                raise ValueError('OAuth cancelled or timed out. Start a new login to retry.')
            tokens = bulk.exchange_code(code, self.verifier)
            if self.cancel.is_set():
                raise ValueError('OAuth cancelled; account was not saved.')
            email = hub.auth_email({'tokens': tokens})
            if not email:
                raise ValueError('OAuth did not return an account email.')
            return bulk.tokens_to_auth(tokens, email)
        finally:
            self.close()

    def close(self):
        self.cancel.set()
        with self.lock:
            server, self.server = self.server, None
        if server:
            server.shutdown()
            server.server_close()
