"""Batch browser OAuth login, adapted from the local import9router workflow.

Passwords/TOTP stay in memory. Each browser intercepts its own PKCE callback;
no shared listener on port 1455 and no writes to 9router.
"""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import hub

CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
REDIRECT_URI = 'http://localhost:1455/auth/callback'
AUTH_ORIGIN = 'https://auth.openai.com'


def parse_accounts(text):
    accounts, seen = [], set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip('\r\n\ufeff')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        separator = '|' if '|' in line else '\t'
        parts = line.split(separator)
        if len(parts) not in (2, 3):
            raise ValueError(f'Line {number}: use email|password|2FA (2FA is optional).')
        email, password = parts[0].strip(), parts[1]
        secret = re.sub(r'[\s-]', '', parts[2]).upper() if len(parts) == 3 else ''
        if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not password:
            raise ValueError(f'Line {number}: a valid email and nonempty password are required.')
        if secret:
            try:
                base64.b32decode(secret.rstrip('=') + '=' * (-len(secret.rstrip('=')) % 8))
            except ValueError:
                raise ValueError(f'Line {number}: 2FA must be a Base32 authenticator secret.') from None
        if email.casefold() not in seen:
            seen.add(email.casefold())
            accounts.append((email, password, secret))
    if not accounts:
        raise ValueError('Enter at least one account.')
    return accounts


def authorization(originator='codex_cli_rs', scope='openid profile email offline_access'):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(32)
    url = AUTH_ORIGIN + '/oauth/authorize?' + urlencode({
        'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'response_type': 'code',
        'scope': scope, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'state': state, 'id_token_add_organizations': 'true',
        'codex_cli_simplified_flow': 'true', 'originator': originator,
    })
    return url, verifier, state


def callback_code(url, state):
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc, parsed.path) != ('http', 'localhost:1455', '/auth/callback'):
        raise ValueError('Unexpected OAuth callback address.')
    query = parse_qs(parsed.query)
    if query.get('state') != [state]:
        raise ValueError('OAuth state did not match this account.')
    if 'error' in query:
        raise ValueError('OAuth authorization was denied or cancelled.')
    if len(query.get('code', [])) != 1:
        raise ValueError('OAuth callback did not contain one authorization code.')
    return query['code'][0]


def tokens_to_auth(tokens, expected_email):
    if not isinstance(tokens, dict) or any(not isinstance(tokens.get(key), str) or not tokens[key] for key in ('access_token', 'refresh_token', 'id_token')):
        raise ValueError('OAuth response is missing credentials.')
    identity = hub.jwt_payload(tokens['access_token']).get('https://api.openai.com/auth', {})
    account_id = identity.get('chatgpt_account_id') if isinstance(identity, dict) else None
    if not isinstance(account_id, str) or not account_id:
        raise ValueError('OAuth response is missing the ChatGPT account ID.')
    auth = {'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None, 'tokens': {
        key: tokens[key] for key in ('access_token', 'refresh_token', 'id_token')},
        'last_refresh': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    auth['tokens']['account_id'] = account_id
    email = hub.auth_email(auth)
    if not email or email.casefold() != expected_email.casefold():
        raise ValueError('Logged-in email does not match the requested account.')
    return auth


def exchange_code(code, verifier):
    request = Request(AUTH_ORIGIN + '/oauth/token', data=urlencode({
        'grant_type': 'authorization_code', 'client_id': CLIENT_ID,
        'code': code, 'redirect_uri': REDIRECT_URI, 'code_verifier': verifier,
    }).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        raise ValueError(f'OAuth token exchange failed (HTTP {error.code}).') from None
    except (URLError, ValueError, OSError):
        raise ValueError('OAuth token exchange failed; check the connection and retry.') from None


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.is_visible():
            return locator
    return None


class RetryableLoginError(ValueError):
    """A fresh OAuth/browser session can recover this failure."""


def submit_form(page, field):
    button = first_visible(page, ['button[type="submit"]'])
    if button is None:
        button = page.get_by_role('button', name=re.compile(
            r'^(Continue|Next|Log in|Sign in|Verify|Submit|Tiếp tục|Đăng nhập)$', re.I)).first
        if not button.is_visible():
            button = None
    if button is not None and button.is_enabled():
        button.click()
    else:
        field.press('Enter')


def drive_login(page, credentials, callback, cancel, progress, timeout=180):
    """Fill official login forms; leave phone, CAPTCHA and email challenges to the user."""
    email, password, secret = credentials
    deadline, completed, last_status = time.monotonic() + timeout, set(), None
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise ValueError('Cancelled.')
        if callback:
            if 'error' in callback:
                raise ValueError(callback['error'])
            return callback['code']
        if page.is_closed():
            raise ValueError('Login browser was closed.')
        origin = urlsplit(page.url)
        status = 'Complete any phone, email or security verification in the browser.'
        if origin.scheme == 'https' and origin.netloc == 'auth.openai.com':
            body = page.locator('body').inner_text(timeout=2000).lower()
            if any(word in body for word in ('invalid_state', 'session ended', 'not valid json', 'oops, an error occurred')):
                raise RetryableLoginError('A temporary OpenAI login page error occurred.')
            if any(word in body for word in ('incorrect password', 'wrong password', 'invalid credentials')):
                raise ValueError('Login rejected: check this account’s password.')
            email_input = first_visible(page, ['input[type="email"]', 'input[name="email"]', 'input[name="username"]', 'input[autocomplete="username"]', 'input[id*="email" i]'])
            password_input = first_visible(page, ['input[type="password"]'])
            otp_input = first_visible(page, ['input[autocomplete="one-time-code"]', 'input[name="code"]',
                                             'input[inputmode="numeric"]', 'input[id*="code" i]',
                                             'input[placeholder*="code" i]', 'input[aria-label*="code" i]'])
            if email_input is not None and 'email' not in completed:
                email_input.fill(email)
                submit_form(page, email_input)
                completed.add('email')
                status = 'Email entered.'
            elif password_input is not None and 'password' not in completed:
                password_input.fill(password)
                submit_form(page, password_input)
                completed.add('password')
                status = 'Password entered.'
            elif otp_input is not None and secret and 'totp' not in completed:
                if any(word in body for word in ('authenticator', 'authentication app', 'ứng dụng xác thực', 'two-factor', 'two factor')) and not any(
                        word in body for word in ('check your email', 'sent to your email', 'sent to your phone', 'text message', 'phone number', 'sms')):
                    import pyotp
                    otp_input.fill(pyotp.TOTP(secret).now())
                    submit_form(page, otp_input)
                    completed.add('totp')
                    status = 'Authenticator code entered.'
            elif email_input is None and password_input is None and otp_input is None:
                consent = page.get_by_role('button', name=re.compile(r'^(Continue|Authorize|Allow|Accept|Tiếp tục|Cho phép)$', re.I)).first
                key = 'consent:' + page.url
                if key not in completed and consent.is_visible():
                    consent.click()
                    completed.add(key)
                    status = 'Authorization submitted.'
        if status != last_status:
            progress(status)
            last_status = status
        page.wait_for_timeout(250)
    if any(key.startswith('consent:') for key in completed):
        raise RetryableLoginError('OAuth callback timed out after authorization.')
    raise ValueError('Login timed out. Finish verification and retry this account.')


def capture_callback(page, state, callback):
    # Chromium Fetch intercepts redirects too; Playwright route handlers can skip
    # subsequent requests in a redirect chain, sending them to another port-1455 owner.
    session = page.context.new_cdp_session(page)

    def capture(event):
        try:
            code = callback_code(event['request']['url'], state)
            if not callback:
                callback['code'] = code
        except ValueError as error:
            callback['error'] = str(error)
        session.send('Fetch.fulfillRequest', {'requestId': event['requestId'], 'responseCode': 200,
                     'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html'}],
                     'body': base64.b64encode(b'<h2>Return to Codex CLI Hub</h2><p>Finishing account login...</p>').decode()})

    session.on('Fetch.requestPaused', capture)
    session.send('Fetch.enable', {'patterns': [{'urlPattern': REDIRECT_URI + '*', 'requestStage': 'Request'}]})
    return session


def login_one(credentials, cancel, progress):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError('Bulk add needs Playwright. See docs/LINUX.md for setup.') from None
    try:
        with sync_playwright() as playwright:
            for attempt in range(3):
                if cancel.is_set():
                    raise ValueError('Cancelled.')
                url, verifier, state = authorization()
                callback = {}
                progress(f'Opening login browser ({attempt + 1}/3)…')
                # Match import9router's tested browser launch. Without this flag,
                # the login form returned HTML instead of JSON after email submit.
                launch_args = ['--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                try:
                    browser = playwright.chromium.launch(channel='chrome', headless=False, args=launch_args)
                except Exception:
                    browser = playwright.chromium.launch(headless=False, args=launch_args)
                try:
                    context = browser.new_context(viewport={'width': 1280, 'height': 800}, service_workers='block')
                    # Per-browser interception avoids port conflicts and cross-account callbacks.
                    page = context.new_page()
                    capture_callback(page, state, callback)
                    page.set_default_timeout(5000)
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    code = drive_login(page, credentials, callback, cancel, progress)
                    if cancel.is_set():
                        raise ValueError('Cancelled.')
                    progress('Finishing OAuth login…')
                    tokens = exchange_code(code, verifier)
                    if cancel.is_set():
                        raise ValueError('Cancelled.')
                    return tokens_to_auth(tokens, credentials[0])
                except RetryableLoginError:
                    if attempt == 2:
                        raise
                    progress('Temporary OAuth error; restarting login…')
                finally:
                    browser.close()
                if cancel.wait(1):
                    raise ValueError('Cancelled.')
    except ValueError:
        raise
    except Exception:
        # Playwright exceptions can contain passwords, OTP values or callback URLs.
        raise ValueError('Browser login failed or was closed. Check the browser installation and retry.') from None


def run_batch(accounts, workers, cancel, emit):
    """Emit per-row progress and auth in memory; the GUI alone writes the account store."""
    if not 1 <= workers <= 6:
        raise ValueError('Choose 1–6 simultaneous browsers.')

    def run(index, credentials):
        if cancel.is_set():
            emit(index, 'Cancelled', None)
            return
        try:
            auth = login_one(credentials, cancel, lambda status: emit(index, status, None))
            emit(index, 'Authenticated', auth)
        except ValueError as error:
            emit(index, str(error), None)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='bulk-login') as executor:
        futures = [executor.submit(run, index, credentials) for index, credentials in enumerate(accounts)]
        accounts.clear()
        for future in as_completed(futures):
            future.result()
