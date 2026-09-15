"""Long-email cards must fit a narrow list, including badges, bars and actions."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'linux'))
import app
import hub
from test_bulk import auth

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root/'home/sessions').mkdir(parents=True)
    store = hub.Store(root/'data')
    store.settings['default_home'] = str(root/'home')
    email = 'very.long.account.name.for.layout.testing@example-company-with-long-domain.com'
    account = store.create('x', '', auth_data=json.dumps(auth(email)).encode())
    account.update(subscription='Enterprise', quota=['codex · 5 hours: 80% left'])
    window = tk.Tk()
    ui = app.Application(window, store)
    ui.quota_due[account['id']] = time.monotonic()+3600
    for detail, widths in ((False, (900, 1180, 1500)), (True, (1500,))):
        if detail:
            ui.select_account(account)
        for width in widths:
            window.geometry(f'{width}x720')
            window.update()
            row = ui.account_rows[0]
            assert row['name'].cget('text') != email, 'Long email must be clipped in the compact row'
            assert row['name'].cget('text').endswith('...')
            assert row['name'].cget('wraplength') == 0
            left = ui.canvas.winfo_rootx()
            right = left + ui.canvas.winfo_width()
            def check(widget):
                if not widget.winfo_ismapped():
                    return
                assert widget.winfo_rootx() >= left, str(widget)
                assert widget.winfo_rootx()+widget.winfo_width() <= right, f'Clipped widget: {widget}'
                for child in widget.winfo_children():
                    check(child)
            check(row['frame'])
            children = row['frame'].grid_slaves()
            assert children and all(child.grid_info()['row'] == 0 for child in children), 'Account row must be one visual grid row'
            assert row['frame'].winfo_height() <= 60, f"Account row is too tall: {row['frame'].winfo_height()}"
            def center_y(widget):
                return widget.winfo_rooty() + widget.winfo_height() // 2
            assert abs(center_y(row['subscription']) - center_y(row['name'])) <= 3
            assert abs(center_y(row['expiry']) - center_y(row['name'])) <= 3
            identity = row['frame']
            account['quota'] = ['codex · 5 hours: 70% left']
            ui.update_account_quota(account)
            window.update()
            assert ui.account_rows[0]['frame'] is identity
    window.destroy()
    store.close()
print('Card layout passed: full long email, badges/bars/actions within 900/1180/1500px windows, detail open/closed, in-place quota.')
