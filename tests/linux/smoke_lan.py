"""Opt-in real local-interface test. Synthetic data/keys only; no upstream calls."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub
import pools

networks = pools.network_interfaces()
assert networks, 'No connected private IPv4 interface to test'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with tempfile.TemporaryDirectory(prefix='hub-lan-probe-') as temporary:
    root = Path(temporary)
    store = hub.Store(root)
    store.close()
    config = pools.load_config(root)
    for i, policy in enumerate(config['pools']):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            policy['port'] = sock.getsockname()[1]
        policy['interface'] = networks[i % len(networks)]['interface']
    pools.save_config(root, config)
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[2] / 'linux/pool_server.py'), '--data-dir', str(root)])
    try:
        for i, policy in enumerate(config['pools']):
            host = networks[i % len(networks)]['addresses'][0]
            url = f'http://{host}:{policy["port"]}/health'
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer '+policy['key']})
            deadline = time.monotonic() + 8
            while True:
                try:
                    with opener.open(request, timeout=1) as response:
                        assert json.load(response)['pool'] == policy['id']
                    break
                except urllib.error.URLError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
            try:
                opener.open(url, timeout=1)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError('Unauthenticated LAN request allowed')
            print(policy['id'], 'LAN health/key verified on', policy['interface'], flush=True)
        for policy in config['pools']:
            policy['interface'] = ''
        pools.save_config(root, config)
        deadline = time.monotonic() + 8
        while True:
            try:
                opener.open(request, timeout=1).close()
            except urllib.error.URLError:
                break
            assert time.monotonic() < deadline, 'LAN listener not removed'
            time.sleep(0.1)
        print('LAN disabled without restarting server.', flush=True)
    finally:
        process.terminate()
        process.wait(timeout=25)
