"""Tabs hide rows without rebuilding widgets or stopping hidden quota updates."""
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
    (Path(temp) / 'home/sessions').mkdir(parents=True)
    store = hub.Store(Path(temp) / 'data')
    store.settings['default_home'] = str(Path(temp) / 'home')
    for i in range(30):
        a = store.create('x', '', auth_data=json.dumps(auth(f'u{i}@example.com')).encode())
        a['subscription'] = 'Plus' if i < 15 else 'Free'
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic() + 3600 for a in store.accounts}
    window.update()
    assert hasattr(ui, 'active_tier'), 'Tier tabs missing'
    rows = [r['frame'] for r in ui.account_rows]
    ui.select_tier('Plus')
    ui.select_account(store.accounts[0])
    window.update()
    assert rows == [r['frame'] for r in ui.account_rows]
    assert rows[0].winfo_ismapped() and not rows[15].winfo_ismapped()
    detail = ui.detail_body.winfo_children()
    ui.detail_close.focus_force()
    ui.canvas.yview_moveto(.3)
    window.update()
    scroll = ui.canvas.yview()
    ui.select_tier('Free')
    ui.select_tier('Plus')
    window.update()
    assert ui.canvas.yview() == scroll
    ui.status.set('Unrelated')
    with patch.object(ui, 'background', side_effect=lambda op, ok, fail=None: ok(op())), patch('hub.refresh', return_value={'quota': ['codex · Weekly: 42% left']}):
        ui.refresh(store.accounts[0])
        ui.quota_due[store.accounts[15]['id']] = 0
        ui.auto_refresh()
    window.update()
    assert rows == [r['frame'] for r in ui.account_rows]
    assert ui.detail_body.winfo_children() == detail
    assert ui.active_tier.get() == 'Plus' and ui.canvas.yview() == scroll
    assert window.focus_get() == ui.detail_close and ui.status.get() == 'Unrelated'
    assert ui.account_rows[15]['quota_5h'].quota_state == ('W 42%', 42)
    store.accounts[0]['subscription'] = 'Pro'
    ui.update_account_quota(store.accounts[0])
    window.update()
    assert 'Pro' in ui.groups and not rows[0].winfo_ismapped()
    assert ui.selected_account_id == store.accounts[0]['id'] and ui.detail_visible
    assert ui.detail_body.winfo_children() == detail
    store.accounts[0]['subscription'] = 'Enterprise'
    ui.update_account_quota(store.accounts[0])
    assert ui.account_rows[0]['subscription'].cget('text') == 'Ent...'
    store.accounts[0]['subscription'] = 'Pro'
    ui.update_account_quota(store.accounts[0])
    ui.select_tier('Pro')
    assert rows[0].winfo_manager()
    store.delete(store.accounts[0])
    ui.render()
    assert ui.active_tier.get() == 'All' and 'Pro' not in ui.groups
    for account, tier in zip(store.accounts, ['Go', 'Plus', 'Pro Lite', 'Pro', 'Pro5x', 'Pro20x',
                                             'Business', 'Enterprise', 'Edu', 'Edu Plus', 'Edu Pro', 'Unknown']):
        account['subscription'] = tier
    ui.render()
    window.update()
    assert hasattr(ui, 'tier_canvas'), 'Many tier tabs need a scrollable strip'
    ui.tier_canvas.xview_moveto(1)
    window.update()
    last = ui.groups['Unknown']
    assert last.winfo_rootx() >= ui.tier_canvas.winfo_rootx()
    assert last.winfo_rootx() + last.winfo_width() <= ui.tier_canvas.winfo_rootx() + ui.tier_canvas.winfo_width()
    window.destroy()
    store.close()
print('Account tabs passed: dynamic tiers, hidden polling, stable rows/details/focus, per-tab scroll and deletion.')
