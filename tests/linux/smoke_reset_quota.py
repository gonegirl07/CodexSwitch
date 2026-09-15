"""xvfb-run -a python3 tests/linux/smoke_reset_quota.py (no live reset)."""
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
    (root / 'default/sessions').mkdir(parents=True)
    store = hub.Store(root / 'data')
    store.settings.update(default_home=str(root / 'default'), codex=str(Path(__file__).with_name('fake_codex.py').resolve()))
    account = store.create('x', '', auth_data=json.dumps(auth('test@example.com')).encode())
    account['quota'] = ['gpt-reserve · Weekly: 100% left', 'codex · Weekly: 23% left', 'Reset count: 3']
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due[account['id']] = time.monotonic() + 3600
    ui.select_account(account)
    window.update()
    assert ui.account_rows[0]['reset'].cget('text') == 'Reset(3)'
    assert ui.quota_summary(account, 'Weekly', 'W') == ('W 23%', 23)
    assert ui.detail_reserve.winfo_ismapped()
    assert ui.detail_reserve.quota_state == ('GPT Reserve weekly 100%', 100)
    rows = [str(r['frame']) for r in ui.account_rows]
    reserve = ui.detail_reserve
    ui.detail_close.focus_force()
    window.update()
    focus, scroll = window.focus_get(), ui.canvas.yview()
    account['quota'][0] = 'gpt-reserve · Weekly: 75% left'
    ui.update_account_quota(account)
    window.update()
    assert reserve is ui.detail_reserve and reserve.quota_state[1] == 75
    assert window.focus_get() == focus and ui.canvas.yview() == scroll
    assert [str(r['frame']) for r in ui.account_rows] == rows
    account['quota'] = account['quota'][1:]
    ui.update_account_quota(account)
    window.update()
    assert not reserve.winfo_ismapped()
    pending = []
    with patch.object(ui, 'background', side_effect=lambda op, done, fail=None: pending.append((op, done, fail))), \
            patch('app.messagebox.askyesno', return_value=False) as confirm:
        ui.reset_account(account)
        assert confirm.called and not pending
    with patch('app.messagebox.askyesno', return_value=True), \
            patch.object(ui, 'background', side_effect=lambda op, done, fail=None: pending.append((op, done, fail))):
        ui.reset_account(account)
        key = account['reset_pending_key']
        ui.reset_account(account)
        assert len(pending) == 1, 'Double-click submitted two resets'
        assert json.loads((store.root / 'accounts.json').read_text())['accounts'][0]['reset_pending_key'] == key
        op, done, fail = pending.pop()
        with patch('hub.reset_quota', return_value={'quota_error': 'offline'}) as reset:
            done(op())
            assert reset.call_args.args[2] == key
        assert account['reset_pending_key'] == key
        ui.reset_account(account)
        op, done, fail = pending.pop()
        with patch('hub.reset_quota', return_value={'reset_outcome': 'reset', 'quota_error': None,
                   'quota': ['codex · Weekly: 100% left', 'Reset count: 2']}) as reset:
            done(op())
            assert reset.call_args.args[2] == key, 'Retry spent a new reset attempt'
        assert 'reset_pending_key' not in account
        assert hub.reset_count(account['quota']) == 2
    account['quota'] = ['Reset count: 0']
    ui.update_account_quota(account)
    window.update()
    with patch('app.messagebox.showinfo') as info, patch.object(ui, 'background') as worker:
        ui.reset_account(account)
        assert info.called and not worker.called
    assert ui.account_rows[0]['reset'].cget('text') == 'Reset(0)'
    with patch.object(tk.Menu, 'tk_popup') as popup:
        ui.more(account)
        menus = [w for w in window.winfo_children() if isinstance(w, tk.Menu)]
        assert any(m.entrycget(i, 'label') == 'Reset(0)' for m in menus for i in range(m.index('end') + 1))
    window.destroy()
    store.close()
print('Reset/quota Tk smoke passed: selection, reserve visibility, confirmation, duplicate guard, idempotent retry, menu count.')
