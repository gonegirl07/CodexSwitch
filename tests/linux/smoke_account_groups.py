"""Synthetic Tk regression: groups, tier changes, local/pool tags, silent refresh."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'linux'))
import app
import hub
import local_switch
from test_bulk import auth, token

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root/'home/sessions').mkdir(parents=True)
    store = hub.Store(root/'data')
    store.settings.update(default_home=str(root/'home'), codex=str(Path(__file__).with_name('fake_codex.py').resolve()))
    for i in range(20):
        a = store.create('x', '', auth_data=json.dumps(auth(f'user{i}@example.com')).encode())
        a['subscription'] = 'Plus' if i < 10 else 'Free'
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic()+3600 for a in store.accounts}
    window.update()
    assert hasattr(ui, 'groups'), 'Subscription groups missing'
    assert '10' in ui.groups['Plus'].cget('text')
    account = store.accounts[5]
    ui.select_account(account)
    ui.select_tier('Plus')
    window.update()
    assert not ui.account_rows[15]['frame'].winfo_ismapped()
    ui.canvas.yview_moveto(.2)
    ui.detail_close.focus_force()
    window.update()
    rows = [r['frame'] for r in ui.account_rows]
    detail = list(ui.detail_body.winfo_children())
    scroll, focus = ui.canvas.yview(), window.focus_get()
    ui.status.set('Keep unrelated message')
    result = {'quota': ['codex · 5 hours: 70% left'], 'quota_error': None}
    def background(operation, success, failure=None):
        success(operation())
    with patch.object(ui, 'background', side_effect=background), patch('hub.refresh', return_value=result), patch('app.messagebox.showerror', side_effect=AssertionError('Dialog')):
        ui.refresh(account)
        ui.quota_due[store.accounts[15]['id']] = 0
        ui.auto_refresh()
        window.update()
        assert [r['frame'] for r in ui.account_rows] == rows
        assert list(ui.detail_body.winfo_children()) == detail
        assert ui.canvas.yview() == scroll and window.focus_get() == focus
        assert ui.account_rows[15]['quota_5h'].itemcget('label', 'text') == '5h 70%'
        result['quota_error'] = 'offline'
        ui.refresh(account)
        assert 'stale' in ui.account_rows[5]['quota_5h'].itemcget('label', 'text')
        result.update(subscription='Free', quota_error=None)
        ui.refresh(account)
        window.update()
        assert [r['frame'] for r in ui.account_rows] == rows
        assert list(ui.detail_body.winfo_children()) == detail
        assert ui.selected_account_id == account['id'] and ui.detail_visible
        assert window.focus_get() == focus
        assert '11' in ui.groups['Free'].cget('text')
        assert not ui.account_rows[5]['frame'].winfo_ismapped()
        assert ui.status.get() == 'Keep unrelated message'
        rotated = auth(account['email'])
        rotated['tokens']['id_token'] = token({'email': account['email'],
            'https://api.openai.com/auth': {'chatgpt_plan_type': 'pro'}})
        hub.atomic_write(store.profile(account)/'auth.json', json.dumps(rotated).encode())
        result['subscription'] = 'Pro20x'
        ui.refresh(account)
        assert account['subscription'] == 'Pro20x', 'Broad token tier overwrote fresh quota tier'
        assert ui.account_rows[5]['subscription'].cget('text') == 'Pro20x'
    local_switch.switch_account(store, account)
    ui.update_activity()
    assert ui.account_rows[5]['activity'].cget('text') == 'current'
    activity_path = store.root/'pool-status.json'
    hub.atomic_write(activity_path, json.dumps({'pid': os.getpid(), 'updated_at': time.time(),
        'inflight_by_pool': {'0': {account['id']: 2}, '1': {account['id']: 1}}}).encode())
    ui.update_activity()
    window.update()
    assert ui.account_rows[5]['activity'].cget('text') == 'current · current pool 1 · current pool 2'
    assert [r['frame'] for r in ui.account_rows] == rows
    assert list(ui.detail_body.winfo_children()) == detail and window.focus_get() == focus
    with patch.object(ui.account_rows[5]['activity'], 'configure', wraps=ui.account_rows[5]['activity'].configure) as redraw:
        ui.update_activity()
        assert not redraw.called, 'Unchanged activity tag redrawn'
    hub.atomic_write(activity_path, b'{}')
    ui.update_activity()
    assert ui.account_rows[5]['activity'].cget('text') == 'current'
    ui.select_tier('Pro20x')
    window.update()
    assert ui.account_rows[5]['frame'].winfo_ismapped()
    ui.select_tier('Plus')
    added = store.create('x', '', auth_data=json.dumps(auth('new@example.com')).encode())
    ui.render()
    assert ui.active_tier.get() == 'Plus'
    store.delete(added)
    ui.render()
    assert 'Unknown' not in ui.groups and ui.active_tier.get() == 'Plus'
    window.destroy()
    store.close()
print('Account tier regression passed: counts/tabs, hidden polling, tier move, stable rows/details/focus, local current, add/delete.')
