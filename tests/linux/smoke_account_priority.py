"""Actual Tk priority ordering and subscription countdown, synthetic profiles only."""
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
    (root/'home/sessions').mkdir(parents=True)
    store = hub.Store(root/'data')
    store.settings['default_home'] = str(root/'home')
    for i in range(20):
        account = store.create('x', '', auth_data=json.dumps(auth(f'user{i}@example.com')).encode())
        account.update(subscription='Plus', subscription_active_until=time.time()+5*3600+30)
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic()+3600 for a in store.accounts}
    ui.select_account(store.accounts[3])
    window.update()
    ui.canvas.yview_moveto(.3)
    ui.detail_close.focus_force()
    window.update()
    rows = [r['frame'] for r in ui.account_rows]
    details = list(ui.detail_body.winfo_children())
    scroll, focus = ui.canvas.yview(), window.focus_get()
    with patch('local_switch.current_account', return_value=store.accounts[5]['id']), patch('local_switch.pool_activity', return_value={store.accounts[7]['id']: ['current pool 2']}):
        ui.update_activity()
        window.update()
        ordered = [w for w in ui.cards.pack_slaves() if w in rows]
        assert ordered[:3] == [rows[5], rows[7], rows[0]], 'Current accounts not promoted'
        assert [r['frame'] for r in ui.account_rows] == rows
        assert list(ui.detail_body.winfo_children()) == details
        assert window.focus_get() == focus and ui.canvas.yview() == scroll
        assert ui.selected_account_id == store.accounts[3]['id']
        assert ui.account_rows[3]['expiry'].cget('text') == '5h'
        store.accounts[3]['subscription_active_until'] = time.time()+1800
        ui.update_activity()
        assert ui.account_rows[3]['expiry'].cget('text') == '<1h'
        with patch.object(ui, 'layout_groups', wraps=ui.layout_groups) as layout:
            ui.update_activity()
            assert not layout.called, 'Unchanged priority unnecessarily rearranged'
        ui.select_tier('Plus')
        ui.update_activity()
        assert ui.active_tier.get() == 'Plus' and rows[5].winfo_ismapped()
    window.destroy()
    store.close()
print('Priority/countdown smoke passed: stable widgets, focus, scroll, selection, tabs and unchanged updates.')
