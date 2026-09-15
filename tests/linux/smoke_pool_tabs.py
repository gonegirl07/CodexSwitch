"""Pool tabs, membership from the tab, and compact Reset/priority widgets."""
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
import pools
from test_bulk import auth

with tempfile.TemporaryDirectory() as temp:
    (Path(temp) / 'home/sessions').mkdir(parents=True)
    store = hub.Store(Path(temp) / 'data')
    store.settings['default_home'] = str(Path(temp) / 'home')
    plus = store.create('x', '', auth_data=json.dumps(auth('plus@example.com')).encode())
    free = store.create('x', '', auth_data=json.dumps(auth('free@example.com')).encode())
    plus.update(subscription='Plus', priority=0, quota=['Reset count: 2'])
    free.update(subscription='Free', quota=['Reset count: 0'])
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic() + 3600 for a in store.accounts}
    window.update()
    assert 'Pool 1' in ui.groups and 'Pool 2' in ui.groups
    assert ui.account_rows[0]['reset'].cget('text') == 'Reset(2)'
    plus_row = next(row for row in ui.account_rows if row['id'] == plus['id'])
    assert plus_row['subscription'].cget('text') == 'Plus'
    assert not plus_row['priority'].winfo_ismapped(), 'Priority column is pool-tab only'
    assert plus_row['priority'].cget('text') != 'P0' if plus_row['priority'].winfo_class() == 'TLabel' else True

    def texts(widget):
        values = []
        try:
            values.append(str(widget.cget('text')))
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            values.extend(texts(child))
        return values

    chips = [text for text in texts(plus_row['frame']) if len(text) >= 2 and text[0] == 'P' and text[1:].isdigit()]
    assert chips == [], f'Plan tabs must not show P-priority chips: {chips}'
    def tab_texts():
        return [child.cget('text') for child in ui.tier_tabs.pack_slaves()
                if child.winfo_ismapped() and child.winfo_class() in ('TButton', 'Button')]
    assert '+' in tab_texts(), 'Hub tab strip must have + to create a pool'
    assert '−' in tab_texts(), 'Hub tab strip must have − to delete a pool'
    assert tab_texts().index('+') == tab_texts().index('Pool 2 (0)') + 1
    assert tab_texts().index('−') == tab_texts().index('+') + 1
    ui.select_tier('All')
    window.update()
    assert ui.delete_pool_button.instate(['disabled'])
    ui.add_pool_button.invoke()
    window.update()
    assert len(pools.load_config(store.root)['pools']) == 3
    assert 'Pool 3' in ui.groups
    assert ui.active_tier.get() == 'Pool 3'
    assert tab_texts().index('+') == tab_texts().index('Pool 3 (0)') + 1
    assert not ui.delete_pool_button.instate(['disabled'])
    ui.pool_settings()
    dialog = ui.pool_dialog
    dialog.create_pool()
    window.update()
    assert len(pools.load_config(store.root)['pools']) == 4
    assert 'Pool 4' in ui.groups
    ui.select_tier('Pool 1')
    window.update()
    assert ui.active_pool_index() == 0
    assert ui.pool_bar.winfo_ismapped()
    bar_buttons = [child.cget('text') for child in ui.pool_bar.winfo_children()
                   if child.winfo_class() in ('TButton', 'Button')]
    assert bar_buttons == ['Add/Remove']
    assert not ui.pool_toggle.instate(['disabled'])
    ui.pool_toggle.invoke()
    window.update()
    picker = ui.pool_picker
    assert picker.window.winfo_exists()
    labels = texts(picker.window)
    for required in ('Available accounts (not in this pool)', 'Accounts in this pool', 'Search email',
                     'Add →', '← Remove', 'Select all', 'All', 'Plus', 'Free', 'Other'):
        assert required in labels, required
    assert labels.count('Select all') == 2
    picker.filters['available']['tier'].set('Plus')
    window.update()
    assert picker.available_ids == [plus['id']]
    picker.select_all_available.invoke()
    window.update()
    assert picker.available.curselection() == (0,)
    picker.add_button.invoke()
    window.update()
    assert pools.load_config(store.root)['pools'][0]['members'] == [plus['id']]
    picker.filters['available']['tier'].set('All')
    picker.filters['available']['query'].set('FREE')
    window.update()
    assert picker.available_ids == [free['id']]
    picker.select_all_available.invoke()
    picker.add_button.invoke()
    window.update()
    assert set(pools.load_config(store.root)['pools'][0]['members']) == {plus['id'], free['id']}
    picker.filters['members']['query'].set('plus')
    window.update()
    assert picker.visible_member_ids == [plus['id']]
    picker.select_all_members.invoke()
    picker.remove_button.invoke()
    window.update()
    assert pools.load_config(store.root)['pools'][0]['members'] == [free['id']]
    assert plus in store.accounts
    picker.window.destroy()
    window.update()
    ui.select_tier('Pool 1')
    window.update()
    mapped = [row['id'] for row in ui.account_rows if row['frame'].winfo_ismapped()]
    assert mapped == [free['id']]
    free_row = next(row for row in ui.account_rows if row['id'] == free['id'])
    assert free_row['priority'].winfo_ismapped()
    assert free_row['priority'].get() == '1'
    assert str(int(float(free_row['priority'].cget('from')))) == '0'
    assert str(int(float(free_row['priority'].cget('to')))) == '4'
    free_row['priority'].invoke('buttonup')
    window.update()
    assert hub.account_priority_value(free) == 2
    assert free_row['priority'].get() == '2'
    assert ui.pool_toggle.cget('text') == 'Add/Remove'
    ui.select_tier('Pool 4')
    window.update()
    with patch('app.messagebox.askyesno', return_value=True):
        ui.delete_pool_button.invoke()
    window.update()
    assert len(pools.load_config(store.root)['pools']) == 3
    assert 'Pool 4' not in ui.groups
    ui.select_tier('Pool 3')
    window.update()
    with patch('app.messagebox.askyesno', return_value=True):
        ui.delete_pool_button.invoke()
    window.update()
    assert len(pools.load_config(store.root)['pools']) == 2
    ui.select_tier('Pool 1')
    window.update()
    assert ui.delete_pool_button.instate(['disabled'])
    plus['quota_error'] = '401 · token_invalidated'
    ui.update_account_quota(plus)
    ui.select_tier('All')
    window.update()
    plus_row = next(row for row in ui.account_rows if row['id'] == plus['id'])
    assert plus_row['error'].cget('text') == '401'
    assert plus_row['error'].winfo_ismapped()
    ui.select_account(plus)
    window.update()
    assert '401' in ui.detail_error.cget('text') and 'token_invalidated' in ui.detail_error.cget('text')
    # Plus without secrets must not start automatic re-login.
    plus['status'] = 'Not logged in'
    with patch.object(ui, 'background') as worker:
        ui.maybe_relogin(plus, {'status': 'Not logged in', 'quota_error': 'Log in before reading quota.'})
        assert not worker.called
    store.save_login(plus, 'plus@example.com', 'secret', '')
    with patch.object(ui, 'background') as worker:
        ui.maybe_relogin(plus, {'quota_error': '403 · forbidden'})
        assert not worker.called, '403 must not trigger automatic re-login'
    pending = []
    with patch.object(ui, 'background', side_effect=lambda op, done, fail=None: pending.append((op, done, fail))):
        ui.maybe_relogin(plus, {'status': 'Not logged in', 'quota_error': 'Log in before reading quota.'})
        assert pending
        ui.maybe_relogin(plus, {'quota_error': '401 · token_invalidated'})
        assert len(pending) == 1, 'One relogin at a time'
        done = pending[0][1]
        with patch('bulk.login_one', return_value=auth('plus@example.com')), patch.object(ui, 'refresh') as refresh:
            done(auth('plus@example.com'))
            assert refresh.called
    window.destroy()
    store.close()
print('Pool tabs passed: +/− pools, dual-list Add/Remove with Select all, priority, compact reset, relogin skip/start.')
