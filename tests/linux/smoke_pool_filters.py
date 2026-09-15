"""Offline Tk: filtered moves use visible IDs; quick actions preserve pending edits."""
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
    for email, tier in [('alpha@example.com', 'Free'), ('beta@example.com', 'Plus'),
                        ('bravo@example.com', 'Plus'), ('pro@example.com', 'Pro')]:
        a = store.create('x', '', auth_data=json.dumps(auth(email)).encode())
        a['subscription'] = tier
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due = {a['id']: time.monotonic() + 3600 for a in store.accounts}
    ui.pool_settings()
    dialog = ui.pool_dialog
    assert 'filters' in dialog.views[0], 'Both membership lists need filters'
    for index, view in enumerate(dialog.views):
        view['filters']['available']['tier'].set('Plus')
        view['filters']['available']['query'].set('  BRAVO  ')
        assert view['available_ids'] == [store.accounts[2]['id']]
        view['available'].selection_set(0)
        dialog.move_members(index, True)
        assert view['member_ids'] == [store.accounts[2]['id']]
        view['filters']['available']['query'].set('')
        view['available'].selection_set(0)
        dialog.move_members(index, True)
        view['filters']['members']['query'].set('BETA')
        view['members'].selection_set(0)
        dialog.move_members(index, False)
        assert view['member_ids'] == [store.accounts[2]['id']], 'Remove must not use unfiltered index'
        view['filters']['available']['tier'].set('Other')
        assert view['available_ids'] == [store.accounts[3]['id']]
    dialog.save()
    a = store.accounts[0]
    ui.select_account(a)
    view = dialog.views[0]
    view['variables']['min_5h'].set('17')
    view['filters']['available']['tier'].set('Plus')
    view['available'].selection_set(0)
    dialog.move_members(0, True)  # Unsaved beta addition must survive quick action.
    ui.toggle_pool(a, 0)
    assert a['id'] in pools.load_config(store.root)['pools'][0]['members']
    assert store.accounts[1]['id'] in view['member_ids']
    assert view['variables']['min_5h'].get() == '17'
    assert ui.detail_actions['pool-0'].cget('text') == 'Remove from Pool 1'
    dialog.save()
    assert pools.load_config(store.root)['pools'][0]['min_5h'] == 17
    ui.toggle_pool(a, 1)
    ui.toggle_pool(a, 0)
    saved = pools.load_config(store.root)['pools']
    assert a['id'] not in saved[0]['members'] and a['id'] in saved[1]['members']
    assert a in store.accounts
    assert set(ui.detail_actions) >= {'open', 'switch', 'refresh', 'reset', 'login', 'resume', 'folder', 'details', 'delete', 'pool-0', 'pool-1'}
    a['quota'] = ['Reset count: 3']
    ui.update_account_quota(a)
    assert ui.detail_actions['reset'].cget('text') == 'Reset(3)', 'Detail reset count must update with quota'
    remaining, exhausted = store.accounts[1], store.accounts[0]
    remaining.update(quota=['codex · 5 hours: 80% left', 'codex · Weekly: 40% left'], quota_error=None)
    exhausted.update(quota=['codex · 5 hours: 0% left', 'codex · Weekly: 0% left'], quota_error=None)
    ui.select_tier('All')
    ui.filter_query.set('BETA')
    window.update()
    mapped = [row['id'] for row in ui.account_rows if row['frame'].winfo_ismapped()]
    assert mapped == [remaining['id']], 'Main list search must match email case-insensitively'
    ui.filter_query.set('')
    ui.filter_quota.set('Remaining')
    window.update()
    mapped = [row['id'] for row in ui.account_rows if row['frame'].winfo_ismapped()]
    assert remaining['id'] in mapped and exhausted['id'] not in mapped
    ui.filter_quota.set('Exhausted')
    window.update()
    mapped = [row['id'] for row in ui.account_rows if row['frame'].winfo_ismapped()]
    assert exhausted['id'] in mapped and remaining['id'] not in mapped
    stale = store.accounts[2]
    stale.update(quota=['codex · 5 hours: 90% left'], quota_error='offline')
    ui.filter_quota.set('Exhausted')
    window.update()
    mapped = [row['id'] for row in ui.account_rows if row['frame'].winfo_ismapped()]
    assert stale['id'] in mapped, 'Stale/unusable quota counts as exhausted'
    ui.filter_quota.set('All')
    window.update()
    # Menu and detail execute the same real membership action, not a placeholder.
    with patch('app.tk.Menu.tk_popup'):
        ui.more(a)
    menu = [child for child in window.winfo_children() if isinstance(child, tk.Menu)][-1]
    labels = [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1)]
    menu.invoke(labels.index('Remove from Pool 2'))
    assert a['id'] not in pools.load_config(store.root)['pools'][1]['members']
    ui.detail_actions['pool-1'].invoke()
    assert a['id'] in pools.load_config(store.root)['pools'][1]['members']
    # A previously selected account hidden by a filter must not be moved.
    view['filters']['available']['tier'].set('All')
    view['available'].selection_set(0)
    view['filters']['available']['query'].set('no-match')
    before = view['member_ids'][:]
    dialog.move_members(0, True)
    assert view['member_ids'] == before and view['filters']['available']['count'].get().startswith('0/')
    # Failure and external conflict leave disk and pending edits untouched.
    before = (store.root / 'pools.json').read_bytes()
    with patch('pools.save_config', side_effect=OSError('disk unavailable')):
        try:
            ui.toggle_pool(a, 0)
        except OSError:
            pass
        else:
            raise AssertionError('Write failure was ignored')
    assert (store.root / 'pools.json').read_bytes() == before
    external = pools.load_config(store.root)
    external['pools'][1]['min_weekly'] = 19
    pools.save_config(store.root, external)
    try:
        ui.toggle_pool(a, 0)
    except ValueError:
        pass
    else:
        raise AssertionError('External pool edit overwritten')
    assert pools.load_config(store.root)['pools'][1]['min_weekly'] == 19
    # All detail actions remain reachable in a small window by scrolling.
    window.geometry('900x560')
    window.update()
    ui.detail_canvas.yview_moveto(1)
    window.update()
    delete = ui.detail_actions['delete']
    assert delete.winfo_rooty() >= ui.detail_canvas.winfo_rooty()
    assert delete.winfo_rooty() + delete.winfo_height() <= ui.detail_canvas.winfo_rooty() + ui.detail_canvas.winfo_height()
    window.destroy()
    store.close()
print('Pool filters passed: both directions/pools, email+tier, visible IDs, immediate actions and pending edits.')
