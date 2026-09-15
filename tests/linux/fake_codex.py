#!/usr/bin/env python3
"""Protocol fixture: validates requests without contacting an account."""
import json
import os
from pathlib import Path
import sys
import time

assert 'OPENAI_API_KEY' not in os.environ
assert sys.argv[1:3] == ['-c', 'cli_auth_credentials_store="file"']
if sys.argv[3:] == ['login']:
    Path(os.environ['CODEX_HOME'], 'terminal-check.json').write_text(json.dumps({'cwd': str(Path.cwd()), 'home': os.environ['CODEX_HOME']}))
    sys.exit(0)
assert sys.argv[3:] == ['app-server']
assert os.environ['CODEX_HOME'] == str(Path.cwd())
auth = Path.cwd() / 'auth.json'
if auth.exists() and json.loads(auth.read_text()).get('quota_test'):
    assert not (Path.cwd() / 'sessions').exists(), 'Quota must not scan shared sessions'
    assert not (Path.cwd() / 'state_5.sqlite').exists(), 'Quota must not open the profile database'
    assert 'sqlite_home' not in (Path.cwd() / 'config.toml').read_text()
    auth.write_text('{"quota_test": true, "refreshed": true}')
if (Path.cwd() / 'stall').exists():
    time.sleep(10)
request = json.loads(input())
assert request['id'] == 1 and request['method'] == 'initialize'
print(json.dumps({'id': 1, 'result': {}}), flush=True)
assert json.loads(input()) == {'method': 'initialized'}
assert json.loads(input()) == {'id': 2, 'method': 'account/rateLimits/read'}
error_path = Path.cwd() / 'rpc-error.json'
if error_path.exists():
    print(json.dumps({'id': 2, 'error': json.loads(error_path.read_text())}), flush=True)
    sys.exit(0)
print(json.dumps({'method': 'notification', 'params': {}}), flush=True)
count = 0 if (Path.cwd() / 'no-credits').exists() else 1
result = {'rateLimits': {'planType': 'plus', 'primary': {'usedPercent': 40, 'windowDurationMins': 300}},
          'rateLimitResetCredits': {'availableCount': count}}
if not (Path.cwd() / 'reset-test').exists():
    result.pop('rateLimitResetCredits')
print(json.dumps({'id': 2, 'result': result}), flush=True)
line = sys.stdin.readline()
if line:
    assert count == 1, 'Must not consume without credits'
    assert json.loads(line) == {'id': 3, 'method': 'account/rateLimitResetCredit/consume', 'params': {'idempotencyKey': 'test-reset-key'}}
    outcome_path = Path.cwd() / 'reset-outcome'
    outcome = outcome_path.read_text() if outcome_path.exists() else 'reset'
    print(json.dumps({'id': 3, 'result': {'outcome': outcome}}), flush=True)
    if (Path.cwd() / 'reset-read-fail').exists():
        assert json.loads(input()) == {'id': 4, 'method': 'account/rateLimits/read'}
        print(json.dumps({'id': 4, 'error': {'message': 'offline'}}), flush=True)
        sys.exit(0)
    assert json.loads(input()) == {'id': 4, 'method': 'account/rateLimits/read'}
    if outcome in ('reset', 'alreadyRedeemed'):
        result['rateLimits']['primary']['usedPercent'] = 0
        result['rateLimitResetCredits']['availableCount'] = 0
    print(json.dumps({'id': 4, 'result': result}), flush=True)
    assert not sys.stdin.readline(), 'Reset must not retry automatically'
