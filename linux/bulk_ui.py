"""Tk batch dialog; account persistence stays on the GUI thread."""
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import bulk


class BulkDialog:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.cancel = threading.Event()
        self.window = tk.Toplevel(app.window)
        self.window.title('Bulk add · email | password | 2FA')
        self.window.geometry('900x650')
        self.window.transient(app.window)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='One account per line: email|password|2FA', style='Name.TLabel').pack(anchor='w')
        ttk.Label(body, text='2FA secret is optional. Tab-separated rows also work.\nPhone-verified accounts can log in; complete any extra verification in the browser.', wraplength=840).pack(anchor='w', pady=10)
        self.input = tk.Text(body, height=8, wrap='none', undo=False, exportselection=False)
        self.input.pack(fill='x')
        controls = ttk.Frame(body, padding=(0, 10))
        controls.pack(fill='x')
        self.load_button = ttk.Button(controls, text='Load .txt…', command=lambda: app.guard(self.load))
        self.load_button.pack(side='left')
        ttk.Label(controls, text='  Browsers: ').pack(side='left')
        self.workers = tk.StringVar(value='3')
        self.worker_input = ttk.Spinbox(controls, from_=1, to=6, textvariable=self.workers, width=4)
        self.worker_input.pack(side='left')
        self.start_button = ttk.Button(controls, text='Start', command=lambda: app.guard(self.start))
        self.start_button.pack(side='right')
        self.stop_button = ttk.Button(controls, text='Stop', command=self.stop, state='disabled')
        self.stop_button.pack(side='right', padx=8)
        self.rows = ttk.Treeview(body, columns=('email', 'status'), show='headings', selectmode='browse')
        self.rows.heading('email', text='Account email')
        self.rows.heading('status', text='Progress')
        self.rows.column('email', width=280)
        self.rows.column('status', width=540)
        self.rows.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='Passwords and 2FA secrets are stored privately in each profile for automatic re-login. Input is cleared when starting.')
        ttk.Label(body, textvariable=self.status, wraplength=840, padding=(0, 10)).pack(anchor='w')

    def load(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('Account list', '*.txt'), ('All files', '*')])
        if path:
            with open(path, encoding='utf-8-sig') as stream:
                text = stream.read(2 * 1024 * 1024 + 1)
            if len(text) > 2 * 1024 * 1024:
                raise ValueError('Account list is too large (maximum 2 MiB).')
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', text)

    def start(self):
        if self.running:
            return
        try:
            import playwright.sync_api  # noqa: F401
            import pyotp  # noqa: F401
        except ImportError:
            raise ValueError('Install bulk dependencies as described in docs/LINUX.md.') from None
        accounts = bulk.parse_accounts(self.input.get('1.0', 'end-1c'))
        try:
            workers = int(self.workers.get())
        except ValueError:
            raise ValueError('Choose 1–6 simultaneous browsers.') from None
        if not 1 <= workers <= 6:
            raise ValueError('Choose 1–6 simultaneous browsers.')
        self.app.store.shared_target()
        self.rows.delete(*self.rows.get_children())
        existing = {(a.get('email') or a['name']).casefold() for a in self.app.store.accounts}
        pending = []
        self.row_ids, self.emails = [], []
        for account in accounts:
            duplicate = account[0].casefold() in existing
            row = self.rows.insert('', 'end', values=(account[0], 'Already added — skipped' if duplicate else 'Queued'))
            if not duplicate:
                pending.append(account)
                self.row_ids.append(row)
                self.emails.append(account[0])
        self.input.delete('1.0', 'end')
        accounts.clear()
        if not pending:
            self.status.set('All emails already exist. No accounts were changed.')
            return
        self.cancel.clear()
        self.running, self.added = True, 0
        self.pending_logins = list(pending)
        self.input.configure(state='disabled')
        for widget in (self.start_button, self.load_button, self.worker_input):
            widget.state(['disabled'])
        self.stop_button.state(['!disabled'])
        self.status.set('Logging in… Complete extra verification in each browser if requested.')

        def emit(index, status, auth):
            self.app.jobs.put((self.update, (index, status, auth)))

        self.app.background(lambda: bulk.run_batch(pending, workers, self.cancel, emit), self.finished,
                            lambda error: self.finished(error))

    def update(self, event):
        index, status, auth = event
        if auth is not None:
            try:
                account = self.app.store.import_auth(auth, self.emails[index])
                email, password, secret = self.pending_logins[index]
                self.app.store.save_login(account, email, password, secret)
                self.added += 1
                status = 'Added · loading quota'
                self.app.render()
                self.app.guard(lambda: self.app.refresh(account))
            except (OSError, ValueError) as error:
                status = 'Not added: ' + str(error)
        self.rows.item(self.row_ids[index], values=(self.emails[index], status))

    def stop(self):
        self.cancel.set()
        self.stop_button.state(['disabled'])
        self.status.set('Stopping… Active browser/network operations may take up to 30 seconds to return.')

    def finished(self, error):
        self.running = False
        self.input.configure(state='normal')
        for widget in (self.start_button, self.load_button, self.worker_input):
            widget.state(['!disabled'])
        self.stop_button.state(['disabled'])
        self.pending_logins = []
        self.status.set(f'Finished: {self.added} account(s) added. ' + (str(error) if error else 'See each row for results.'))
        if self.app.close_requested:
            self.app.window.destroy()

    def close(self):
        if self.running:
            self.stop()
        else:
            self.input.delete('1.0', 'end')
            self.window.destroy()
