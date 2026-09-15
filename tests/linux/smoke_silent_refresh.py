"""Run with xvfb-run -a; exercises actual refresh completion with synthetic data."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'linux'))
import app
import hub
from test_bulk import auth

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root/'default/sessions').mkdir(parents=True)
    store = hub.Store(root/'data')
    store.settings.update(default_home=str(root/'default'), codex=str(Path(__file__).with_name('fake_codex.py').resolve()))
    for i in range(20):
        store.create('x', '', auth_data=json.dumps(auth(f'user{i}@example.com')).encode())
    window = tk.Tk()
    ui = app.Application(window, store)
    account = store.accounts[5]
    ui.select_account(account)
    ui.quota_due = {a['id']: time.monotonic()+3600 for a in store.accounts}
    window.update()
    ui.canvas.yview_moveto(.4)
    ui.detail_close.focus_force()
    window.update()
    rows = [str(r['frame']) for r in ui.account_rows]
    detail = [str(w) for w in ui.detail_body.winfo_children()]
    scroll, focus = ui.canvas.yview(), window.focus_get()
    ui.status.set('Keep switch confirmation')
    result = {'status':'Logged in (local credentials)', 'quota':['codex · 5 hours: 80% left','codex · Weekly: 90% left'], 'quota_error':None,'checked_at':time.time()}
    def drain():
        while not ui.jobs.empty():
            callback, value = ui.jobs.get_nowait()
            callback(value)
        window.update()
    def background(operation, success, failure=None):
        try: ui.jobs.put((success, operation()))
        except Exception as error: ui.jobs.put((failure, str(error)))
    def stable():
        assert [str(r['frame']) for r in ui.account_rows] == rows, 'Refresh rebuilt account list'
        assert [str(w) for w in ui.detail_body.winfo_children()] == detail, 'Refresh rebuilt details'
        assert ui.canvas.yview() == scroll
        assert window.focus_get() == focus
        assert ui.selected_account_id == account['id'] and ui.detail_visible
        assert ui.status.get() == 'Keep switch confirmation'
    with patch.object(ui, 'background', side_effect=background), patch('hub.refresh', return_value=result), patch('app.messagebox.showerror', side_effect=AssertionError('Unexpected dialog')):
        ui.refresh(account)
        stable()
        drain()
        stable()
        bar = ui.account_rows[5]['quota_5h']
        assert bar.itemcget('label','text') == '5h 80%'
        items = bar.find_all()
        with patch.object(bar, 'itemconfigure', wraps=bar.itemconfigure) as redraw:
            ui.refresh(account)
            drain()
            assert not redraw.called, 'Unchanged quota redrawn'
        assert bar.find_all() == items
        result['quota_error'] = 'offline'
        ui.quota_due[account['id']] = 0
        ui.auto_refresh()
        drain()
        stable()
        assert bar.itemcget('label','text') == '5h 80% · stale'
        with patch('hub.codex_path', side_effect=ValueError('missing')):
            ui.quota_due[account['id']] = 0
            ui.auto_refresh()
            drain()
            stable()
        with patch.object(store, 'save_accounts', side_effect=OSError('disk unavailable')):
            ui.refresh(account)
            drain()
            stable()
            assert 'could not be saved' in account['quota_error']
    assert json.loads((store.root/'accounts.json').read_bytes())['accounts'][5]['quota'] == result['quota']
    window.destroy()
    store.close()
print('Silent refresh passed: manual/auto, unchanged/error, stable widgets/scroll/focus/selection, persisted quota, no notifications.')
