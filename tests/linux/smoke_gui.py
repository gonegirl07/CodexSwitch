"""Run with xvfb-run -a python3 tests/linux/smoke_gui.py; no real account data."""
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import app
import hub


with tempfile.TemporaryDirectory(prefix='hub-gui-') as temporary:
    base = Path(temporary)
    (base / 'default' / 'sessions').mkdir(parents=True)
    store = hub.Store(base / 'data')
    store.settings.update(default_home=str(base / 'default'), working_directory=str(base))
    window = tk.Tk()
    errors = []
    window.report_callback_exception = lambda *error: errors.append(error)
    ui = app.Application(window, store)
    window.update()
    assert ui.summary.get().startswith('0 accounts')
    assert not ui.detail_visible
    assert not ui.detail.winfo_ismapped()
    assert 'Your accounts' in ui.cards.winfo_children()[0].winfo_children()[0].cget('text')
    from test_bulk import auth
    import json
    account = store.create('Unused label', 'Ubuntu · isolated profile', auth_data=json.dumps(auth()).encode())
    account.update(status='Logged in (local credentials)', subscription='Plus', quota=['codex · 5 hours: 75% left', 'codex · Weekly: 92% left'])
    long_email = 'very.long.codex.account.name.for.layout.testing@example-company-with-long-domain.com'
    long_account = store.create('Unused long label', '', auth_data=json.dumps(auth(long_email)).encode())
    long_account.update(status='Logged in (local credentials)', subscription='Free', quota=['codex · 5 hours: 64% left', 'codex · Weekly: 81% left'])
    ui.render()
    window.update()
    assert ui.summary.get().startswith('2 accounts')
    assert ui.account_rows[0]['name'].cget('text') == 'person@example.com'
    assert isinstance(ui.account_rows[0]['quota_5h'], tk.Canvas), 'Quota percentage must be inside the progress canvas'
    assert ui.account_rows[0]['quota_5h'].itemcget('label', 'text') == '5h 75%'
    assert ui.account_rows[0]['quota_weekly'].itemcget('label', 'text') == 'W 92%'
    assert ui.account_rows[1]['name'].cget('text') != long_email
    assert ui.account_rows[1]['name'].cget('text').endswith('...')
    assert ui.account_rows[1]['name'].cget('wraplength') == 0
    assert ui.account_rows[0]['actions'][:2] == ['Open', 'Switch']
    assert ui.account_rows[0]['actions'][2].startswith('Reset(')
    assert ui.account_rows[0]['actions'][-1] == '...'
    assert set(ui.groups) >= {'All', 'Plus', 'Free', 'Pool 1', 'Pool 2'}
    assert ui.groups['Plus'].cget('text') == 'Plus (1)'
    assert ui.groups['Free'].cget('text') == 'Free (1)'
    assert ui.account_rows[0]['subscription'].cget('text') == 'Plus'
    assert ui.account_rows[0]['activity'].cget('text') == ''
    assert ui.account_rows[0]['expiry'].cget('text') == '--'
    assert ui.account_rows[0]['checked'].cget('text') == 'not checked'
    ui.select_tier('Free')
    window.update()
    assert ui.active_tier.get() == 'Free'
    assert not ui.account_rows[0]['frame'].winfo_ismapped()
    assert ui.account_rows[1]['frame'].winfo_ismapped()
    ui.select_tier('All')
    window.update()
    assert ui.active_tier.get() == 'All'
    assert ui.account_rows[0]['frame'].winfo_ismapped()
    for row in ui.account_rows[:2]:
        children = row['frame'].grid_slaves()
        assert children and all(child.grid_info()['row'] == 0 for child in children), 'Account row must use one visual grid row'
        assert row['frame'].winfo_height() <= 60, f"Account row is too tall: {row['frame'].winfo_height()}"
    assert not ui.detail_visible
    assert not ui.detail.winfo_ismapped()
    ui.account_rows[0]['frame'].event_generate('<Button-1>')
    window.update()
    assert ui.detail_visible
    assert ui.detail.winfo_ismapped()
    assert ui.detail_title.cget('text') == 'person@example.com'
    ui.detail_close.invoke()
    window.update()
    assert not ui.detail_visible
    assert not ui.detail.winfo_ismapped()
    ui.account_rows[1]['frame'].event_generate('<Button-1>')
    window.update()
    assert ui.detail_title.cget('text') == long_email
    ui.detail_close.invoke()
    window.update()
    ui.bulk_add()
    assert ui.bulk_dialog.window.winfo_exists()
    store.settings['codex'] = str(Path(__file__).with_name('fake_codex.py').resolve())
    ui.bulk_dialog.input.insert('1.0', 'new@example.com|synthetic-password\nperson@example.com|skip-existing')
    with patch('bulk.login_one', return_value=auth('new@example.com')):
        ui.bulk_dialog.start()
        deadline = time.monotonic() + 10
        while (ui.bulk_dialog.running or ui.busy) and time.monotonic() < deadline:
            window.update()
            time.sleep(0.01)
    assert not ui.bulk_dialog.running
    assert len(store.accounts) == 3
    assert store.accounts[2]['name'] == 'new@example.com'
    assert ui.bulk_dialog.added == 1
    assert ui.bulk_dialog.input.get('1.0', 'end-1c') == ''
    assert (store.profile(store.accounts[2]) / 'auth.json').exists()
    ui.bulk_dialog.close()

    # Pool controls and a real child-server lifecycle, using synthetic accounts.
    import socket
    import urllib.request
    import pool_ui
    ui.pool_settings()
    dialog = ui.pool_dialog
    assert hasattr(dialog, 'move_members'), 'Explicit add/remove pool controls missing'
    sockets = [socket.socket(), socket.socket()]
    for sock, view in zip(sockets, dialog.views):
        sock.bind(('127.0.0.1', 0))
        view['variables']['port'].set(str(sock.getsockname()[1]))
        view['available'].selection_set(0)
        dialog.move_members(dialog.views.index(view), True)
    dialog.views[1]['members'].selection_set(0)
    dialog.move_members(1, False)
    dialog.views[1]['available'].selection_set(1)
    dialog.move_members(1, True)
    assert dialog.views[0]['member_ids'] == [store.accounts[0]['id']]
    assert dialog.views[1]['member_ids'] == [store.accounts[1]['id']]
    for sock in sockets:
        sock.close()
    dialog.views[0]['variables']['min_5h'].set('12')
    dialog.views[1]['variables']['min_weekly'].set('20')
    dialog.views[0]['variables']['min_30d'].set('8')
    dialog.views[1]['variables']['min_other'].set('9')
    dialog.start()
    saved_pools = pool_ui.pools.load_config(store.root)['pools']
    assert saved_pools[0]['min_30d'] == 8 and saved_pools[1]['min_other'] == 9
    try:
        deadline = time.monotonic() + 10
        while not pool_ui.server_pid(store.root) and time.monotonic() < deadline:
            window.update()
            time.sleep(0.02)
        assert pool_ui.server_pid(store.root), 'Pool child failed to start'
        for policy in dialog.config['pools']:
            request = urllib.request.Request(f'http://127.0.0.1:{policy["port"]}/health', headers={'Authorization': 'Bearer '+policy['key']})
            with urllib.request.urlopen(request, timeout=3) as response:
                assert json.load(response)['pool'] == policy['id']
        try:
            ui.delete(account)
        except ValueError as error:
            assert 'all API pools' in str(error)
        else:
            raise AssertionError('Pool member deletion was not blocked')
        if '--screenshot' in sys.argv:
            output = Path(__file__).resolve().parents[2] / 'artifacts'
            output.mkdir(exist_ok=True)
            subprocess.run(['import', '-window', str(dialog.window.winfo_id()), str(output / 'linux-pools-preview.png')], check=True)
    finally:
        dialog.stop()
        dialog.process.wait(timeout=15)
        dialog.window.destroy()
    assert pool_ui.server_pid(store.root) is None

    # Exercise the picker callback using the Open button (synthetic double events are disallowed by Tk).
    def click_open():
        for dialog in window.winfo_children():
            if isinstance(dialog, tk.Toplevel):
                body = dialog.winfo_children()[0]
                tree = next(child for child in body.winfo_children() if isinstance(child, app.ttk.Treeview))
                if tree.get_children():
                    tree.selection_set(tree.get_children()[0])
                    for child in body.winfo_children()[-1].winfo_children():
                        if child.cget('text') == 'Open':
                            child.invoke()
                            return
        window.after(50, click_open)

    window.after(150, click_open)
    assert ui.choose_project() == str(base)
    window.update()
    if '--screenshot' in sys.argv:
        output = Path(__file__).resolve().parents[2] / 'artifacts'
        output.mkdir(exist_ok=True)
        subprocess.run(['import', '-window', str(window.winfo_id()), str(output / 'linux-preview.png')], check=True)
    assert not errors, errors
    window.destroy()
    store.close()
print('GUI smoke passed: email cards, bulk import, pool settings, API listeners, deletion guard, clean shutdown and project picker.')
