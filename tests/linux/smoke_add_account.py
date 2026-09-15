"""Synthetic Tk add-account flow. Run with xvfb-run -a; no live credentials."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import app
import hub
import pools
from test_bulk import auth

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    (base / 'default/sessions').mkdir(parents=True)
    store = hub.Store(base / 'data')
    store.settings['default_home'] = str(base / 'default')
    window = tk.Tk()
    ui = app.Application(window, store)
    assert hasattr(ui, 'add_dialog'), 'New add-account dialog missing'
    with patch.object(ui, 'refresh'):
        ui.pool_settings()
        ui.add()
        dialog = ui.add_dialog
        assert hasattr(dialog, 'login_url') and dialog.login_url.get().startswith('https://auth.openai.com/oauth/authorize?'), 'OAuth link must be ready before opening browser'
        dialog.pool_vars[1].set(True)
        dialog.json_input.insert('1.0', json.dumps([auth(), auth('second@example.com')]))
        dialog.import_json()
        assert len(store.accounts) == 2
        try:
            ui.pool_dialog.save()
        except ValueError:
            pass
        else:
            raise AssertionError('Stale Pools window overwrote new account assignment')
        ui.pool_dialog.window.destroy()
        assert len(pools.load_config(store.root)['pools'][1]['members']) == 2
        assert pools.load_config(store.root)['pools'][0]['members'] == []
        assert dialog.json_input.get('1.0', 'end-1c') == ''
        dialog.json_input.insert('1.0', json.dumps(auth()))
        dialog.import_json()
        assert len(store.accounts) == 2
        hub.atomic_write(base / 'default/auth.json', json.dumps(auth('default@example.com')).encode())
        dialog.copy_default()
        assert store.accounts[-1]['name'] == 'default@example.com'
        with patch('webbrowser.open', return_value=True), patch('bulk.exchange_code', return_value=auth('browser@example.com')['tokens']):
            dialog.start_oauth()
            from urllib.parse import parse_qs, urlsplit
            state = parse_qs(urlsplit(dialog.flow.url).query)['state'][0]
            dialog.callback.set('http://localhost:1455/auth/callback?code=test&state='+state)
            dialog.submit_callback()
            deadline = time.monotonic() + 5
            while dialog.flow and time.monotonic() < deadline:
                window.update()
                time.sleep(.02)
            assert len(store.accounts) == 4
            assert store.accounts[-1]['name'] == 'browser@example.com'
        dialog.close()
        window.destroy()
    store.close()
print('Add dialog passed: JSON batch, duplicate preservation, pool assignment, browser OAuth callback and cleared secrets.')
