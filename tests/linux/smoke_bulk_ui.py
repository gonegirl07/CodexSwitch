"""Real Tk bulk dialog + batch workers + persistence; only OAuth is synthetic."""
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import app
import bulk
import bulk_ui
import hub
from test_bulk import auth


with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    (base / 'default/sessions').mkdir(parents=True)
    store = hub.Store(base / 'data')
    store.settings['default_home'] = str(base / 'default')
    store.import_auth(auth('existing@example.com'), 'existing@example.com')
    window = tk.Tk()
    ui = app.Application(window, store)
    dialog = bulk_ui.BulkDialog(ui)
    ui.bulk_dialog = dialog

    def login(credentials, cancel, progress):
        progress('Opening login browser')
        if credentials[0] == 'failed@example.com':
            raise ValueError('Login rejected')
        return auth(credentials[0])

    with patch.object(bulk, 'login_one', side_effect=login), patch.object(ui, 'refresh'), patch.object(ui, 'auto_refresh'):
        dialog.input.insert('1.0', 'existing@example.com|secret\nnew@example.com|secret\nfailed@example.com|secret')
        dialog.start()
        assert dialog.input.get('1.0', 'end-1c') == ''
        deadline = time.monotonic() + 5
        while dialog.running and time.monotonic() < deadline:
            window.update()
            time.sleep(.01)
        assert not dialog.running
        assert dialog.added == 1
        assert sorted(a['email'] for a in store.accounts) == ['existing@example.com', 'new@example.com']
        statuses = [dialog.rows.item(row)['values'][1] for row in dialog.rows.get_children()]
        assert statuses[0] == 'Already added — skipped'
        assert statuses[1] == 'Added · loading quota'
        assert statuses[2] == 'Login rejected'
        root = store.root
        window.destroy()
        store.close()
        reopened = hub.Store(root)
        assert len(reopened.accounts) == 2
        assert all((reopened.profile(a) / 'auth.json').is_file() for a in reopened.accounts)
        added = next(a for a in reopened.accounts if a['email'] == 'new@example.com')
        login = reopened.load_login(added)
        assert login['email'] == 'new@example.com' and login['password'] == 'secret'
        assert 'secret' not in (reopened.root / 'accounts.json').read_text()
        existing = next(a for a in reopened.accounts if a['email'] == 'existing@example.com')
        assert reopened.load_login(existing) is None
        reopened.close()
print('Bulk Tk passed: workers, progress, cleared input, duplicate skip, failure isolation and persisted imports.')
