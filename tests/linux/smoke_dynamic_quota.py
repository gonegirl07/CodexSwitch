"""Offline Tk regression for one/two-window accounts and silent refresh."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import app
import hub
from test_bulk import auth

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root / 'home/sessions').mkdir(parents=True)
    store = hub.Store(root / 'data')
    store.settings.update(default_home=str(root / 'home'), codex=str(Path(__file__).with_name('fake_codex.py')))
    for i in range(20):
        a = store.create('x', '', auth_data=json.dumps(auth(f'user{i}@example.com')).encode())
        a.update(subscription='Free' if i < 10 else 'Plus', quota=[
            'codex · 43200 minutes: 85% left' if i < 10 else 'codex · Weekly: 60% left'])
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic() + 3600 for a in store.accounts}
    a = store.accounts[5]
    ui.select_account(a)
    window.update()
    bar = ui.account_rows[5]['quota_5h']
    assert bar.itemcget('label', 'text') == '30d 85%', '30d must replace the fixed 5h label'
    assert bar.winfo_width() >= 100, 'Single-window label needs the full quota region'
    assert not ui.account_rows[5]['quota_weekly'].winfo_manager(), 'Absent quota must not display'
    assert ui.detail_quota[0].itemcget('label', 'text') == '30d 85%'
    assert not ui.detail_quota[1].winfo_manager()
    assert ui.account_rows[15]['quota_5h'].itemcget('label', 'text') == 'W 60%'
    ui.canvas.yview_moveto(.3)
    ui.detail_close.focus_force()
    window.update()
    rows = [r['frame'] for r in ui.account_rows]
    details = ui.detail_body.winfo_children()
    focus, scroll = window.focus_get(), ui.canvas.yview()
    ui.status.set('Preserve other action')
    result = {'quota': ['codex · 43200 minutes: 75% left'], 'quota_error': None, 'checked_at': time.time()}
    def background(operation, success, failure=None):
        try:
            ui.jobs.put((success, operation()))
        except Exception as error:
            ui.jobs.put((failure, str(error)))
    def drain():
        while not ui.jobs.empty():
            callback, value = ui.jobs.get_nowait()
            callback(value)
        window.update()
    def stable():
        assert rows == [r['frame'] for r in ui.account_rows]
        assert details == ui.detail_body.winfo_children()
        assert window.focus_get() == focus and ui.canvas.yview() == scroll
        assert ui.selected_account_id == a['id'] and ui.detail_visible
        assert ui.status.get() == 'Preserve other action'
    with patch.object(ui, 'background', side_effect=background), patch('hub.refresh', side_effect=lambda *args: dict(result)):
        ui.refresh(a)
        drain()
        stable()
        assert bar.itemcget('label', 'text') == '30d 75%'
        with patch.object(bar, 'itemconfigure', wraps=bar.itemconfigure) as redraw:
            ui.refresh(a)
            drain()
            assert not redraw.called
        result.clear()
        result.update(quota_error='offline')
        ui.quota_due[a['id']] = 0
        ui.auto_refresh()
        drain()
        stable()
        assert bar.quota_state == ('30d 75% · stale', 75)
        result.update(quota=['codex · 5 hours: 70% left', 'codex · Weekly: 55% left'], quota_error=None)
        ui.refresh(a)
        drain()
        stable()
        assert bar.quota_state == ('5h 70%', 70)
        assert ui.account_rows[5]['quota_weekly'].winfo_manager()
        ui.select_tier('Plus')
        window.update()
        scroll = ui.canvas.yview()
        result.update(quota=['codex · 43200 minutes: 65% left'])
        ui.quota_due[a['id']] = 0
        ui.auto_refresh()
        drain()
        stable()
        assert ui.active_tier.get() == 'Plus' and not rows[5].winfo_ismapped()
        assert bar.quota_state == ('30d 65%', 65)
        assert not ui.detail_quota[1].winfo_manager()
        a['quota_windows'] = [{'id': 'primary', 'label': '30d', 'minutes': 43200,
                               'remaining': 65, 'resets_at': 1900000000}]
        ui.update_account_quota(a)
        window.update()
        assert ui.detail_resets[0].cget('text') == time.strftime('Reset: %d/%m %H:%M', time.localtime(1900000000))
        stable()
    window.destroy()
    store.close()
print('Dynamic quota Tk passed: 30d/weekly-only, absent windows, manual/auto/unchanged/failure, stable layout and detail.')
