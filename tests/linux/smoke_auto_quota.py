"""Synthetic UI scheduler checks; no account/network access."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'linux'))
import app
import hub
from test_bulk import auth

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root/'default/sessions').mkdir(parents=True)
    store = hub.Store(root/'data')
    store.settings['default_home'] = str(root/'default')
    for i in range(3):
        store.create('x', '', auth_data=json.dumps(auth(f'person{i}@example.com')).encode())
    window = tk.Tk()
    ui = app.Application(window, store)
    started = []
    def refresh(account):
        started.append(account['id'])
        ui.busy.add(account['id'])
    with patch.object(ui, 'refresh', side_effect=refresh):
        ui.auto_refresh()
        assert len(started) == 2
        ui.auto_refresh()
        assert len(started) == 2, 'Duplicate in-flight refresh'
        ui.busy.clear()
        ui.auto_refresh()
        assert len(started) == 3, 'Third account starved'
        ui.busy.clear()
        ui.auto_refresh()
        assert len(started) == 3, 'Ignored refresh interval'
    value = ui.quota_summary({'quota':['codex · 5 hours: 80% left'], 'quota_error':'offline'}, '5 hours', '5h')
    assert value == ('5h 80% · stale', 80)
    bar=ui.quota_bar(window,*value)
    assert bar.itemcget('label','text') == '5h 80% · stale'
    window.destroy()
    store.close()
print('Auto quota passed: bounded concurrency, no overlap, fair scheduling and stale values retained.')
