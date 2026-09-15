"""Interactive OAuth and Codex auth.json import; Tk owns all persistence."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

import account_add
import hub


class AddDialog:
    def __init__(self, app):
        self.app, self.flow = app, None
        self.window = tk.Toplevel(app.window)
        self.window.title('Add account · Codex CLI Hub')
        self.window.geometry('860x680')
        self.window.transient(app.window)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Add your Codex accounts', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='Names come from account emails. Existing accounts are never overwritten.').pack(anchor='w', pady=8)
        options = ttk.Frame(body)
        options.pack(fill='x', pady=8)
        ttk.Label(options, text='Add new accounts to:').pack(side='left')
        import pools
        try:
            pool_count = len(pools.load_config(app.store.root)['pools'])
        except (OSError, ValueError):
            pool_count = 2
        self.pool_vars = [tk.BooleanVar(value=False) for _ in range(pool_count)]
        for i, variable in enumerate(self.pool_vars):
            ttk.Checkbutton(options, text=f'Pool {i+1}', variable=variable).pack(side='left', padx=12)
        ttk.Label(options, text='Note:').pack(side='left')
        self.note = tk.StringVar()
        note_limit = (self.window.register(lambda value: len(value) <= 1000), '%P')
        ttk.Entry(options, textvariable=self.note, validate='key', validatecommand=note_limit).pack(side='left', fill='x', expand=True)
        tabs = ttk.Notebook(body)
        tabs.pack(fill='both', expand=True, pady=12)
        oauth, imports, existing, batch = [ttk.Frame(tabs, padding=16) for _ in range(4)]
        for panel, title in [(oauth, 'Browser OAuth'), (imports, 'Import auth.json'), (existing, 'Existing Codex'), (batch, 'Bulk login')]:
            tabs.add(panel, text=title)
        ttk.Label(oauth, text='Sign in on the official OpenAI page in your browser.\nHub does not ask for your password here. OAuth sessions expire after 10 minutes.', wraplength=740).pack(anchor='w', pady=8)
        self.login_url = tk.StringVar()
        ttk.Entry(oauth, textvariable=self.login_url, state='readonly').pack(fill='x', pady=8)
        controls = ttk.Frame(oauth)
        controls.pack(fill='x', pady=10)
        for text, callback in [('Copy login link', self.copy_link), ('Open browser', self.start_oauth), ('New login link', self.new_link), ('Cancel login', self.cancel_oauth)]:
            ttk.Button(controls, text=text, command=lambda fn=callback: app.guard(fn)).pack(side='left', padx=(0, 8))
        ttk.Label(oauth, text='If automatic return fails, paste the complete localhost callback URL here.\nWhen signing in on another device, copy the URL after the browser redirects to localhost.', wraplength=740).pack(anchor='w', pady=12)
        self.callback = tk.StringVar()
        ttk.Entry(oauth, textvariable=self.callback, show='•').pack(fill='x')
        ttk.Button(oauth, text='Finish with callback URL', command=lambda: app.guard(self.submit_callback)).pack(anchor='e', pady=10)
        ttk.Label(imports, text='Select one or more Codex auth.json files, or paste an object / JSON array.\nChatGPT OAuth credentials only; API-key-only files cannot join account pools.', wraplength=740).pack(anchor='w')
        self.json_input = tk.Text(imports, height=9, wrap='none', undo=False, exportselection=False)
        self.json_input.pack(fill='both', expand=True, pady=10)
        buttons = ttk.Frame(imports)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Choose auth.json files…', command=lambda: app.guard(self.load_files)).pack(side='left')
        ttk.Button(buttons, text='Import pasted JSON', command=lambda: app.guard(self.import_json)).pack(side='right')
        ttk.Label(existing, text='Copy only auth.json from the default Codex home configured in Settings.\nThe original login and config stay unchanged.', wraplength=740).pack(anchor='w', pady=12)
        ttk.Button(existing, text='Import default Codex login', command=lambda: app.guard(self.copy_default)).pack(anchor='w')
        ttk.Label(batch, text='Use the existing email | password | 2FA workflow for multiple browser logins.\nAssign those accounts from Pools after the batch completes.', wraplength=740).pack(anchor='w', pady=12)
        ttk.Button(batch, text='Open bulk login', command=lambda: app.guard(self.open_bulk)).pack(anchor='w')
        self.status = tk.StringVar(value='No account is saved until login or import succeeds. Keep auth.json private.')
        ttk.Label(body, textvariable=self.status, wraplength=810).pack(fill='x', pady=10)
        ttk.Button(body, text='Close', command=self.close).pack(anchor='e')
        self.start_oauth(open_browser=False)

    def selection(self):
        return [i for i, variable in enumerate(self.pool_vars) if variable.get()], self.note.get()

    def require_idle(self):
        if self.flow:
            self.cancel_oauth()

    def save(self, documents, selection=None):
        selected, note = selection if selection is not None else self.selection()
        try:
            added, skipped, warning = account_add.import_accounts(self.app.store, documents, selected, note)
        finally:
            self.app.render()
        self.status.set(f'Added {len(added)} · Skipped {len(skipped)} duplicates. '+warning)
        self.app.status.set(self.status.get())
        if self.app.pool_dialog and self.app.pool_dialog.window.winfo_exists():
            self.app.status.set(self.status.get()+' Reopen Pools to view newly assigned accounts.')
        for account in added:
            self.app.guard(lambda a=account: self.app.refresh(a))

    def import_json(self):
        self.require_idle()
        documents = account_add.parse_auth_json(self.json_input.get('1.0', 'end-1c'))
        self.json_input.delete('1.0', 'end')
        self.save(documents)

    def load_files(self):
        self.require_idle()
        paths = filedialog.askopenfilenames(parent=self.window, filetypes=[('Codex auth JSON', '*.json'), ('All files', '*')])
        if not paths:
            return
        if len(paths) > 100:
            raise ValueError('Select at most 100 files.')
        documents = []
        size = 0
        for path in paths:
            raw = hub.read_file(path, account_add.MAX_JSON + 1)
            size += len(raw)
            if size > account_add.MAX_JSON:
                raise ValueError('Selected JSON files exceed 2 MiB combined.')
            try:
                documents.extend(account_add.parse_auth_json(raw.decode('utf-8-sig')))
            except UnicodeError:
                raise ValueError('auth.json must be UTF-8 JSON.') from None
        self.save(documents)

    def copy_default(self):
        self.require_idle()
        path = hub.ordinary(Path(self.app.store.settings['default_home']) / 'auth.json')
        raw = hub.read_file(path, account_add.MAX_JSON + 1)
        try:
            self.save(account_add.parse_auth_json(raw.decode('utf-8-sig')))
        except UnicodeError:
            raise ValueError('auth.json must be UTF-8 JSON.') from None

    def start_oauth(self, open_browser=True):
        if self.flow:
            if open_browser:
                url = self.flow.url
                flow = self.flow
                def opened_existing(result):
                    if not result and self.flow is flow and self.window.winfo_exists():
                        self.status.set('Browser did not open. Copy login link and paste it into your browser.')
                self.app.background(lambda: webbrowser.open(url), opened_existing, lambda _: opened_existing(False))
            return
        self.app.store.shared_target()
        selection = self.selection()
        self.app.store.check_name('OAuth account', selection[1])
        flow = account_add.OAuthFlow()
        automatic = flow.listen()
        self.flow = flow
        self.login_url.set(flow.url)
        self.status.set('Login link ready. Copy it into any browser; waiting for authorization…' if automatic else 'Link ready. Port 1455 is in use: paste the callback URL here after login. Hub will not stop the other listener.')

        def finished(result):
            if self.flow is not flow or not self.window.winfo_exists():
                return
            self.flow = None
            self.login_url.set('')
            self.callback.set('')
            self.save([result])

        def failed(error):
            if self.flow is flow and self.window.winfo_exists():
                self.flow = None
                self.login_url.set('')
                self.callback.set('')
                self.status.set(error)

        self.app.background(flow.finish, finished, failed)
        def opened(result):
            if not result and self.flow is flow and self.window.winfo_exists():
                self.status.set('Browser did not open. Use Copy login link, then paste it into your browser.')
        if open_browser:
            self.app.background(lambda: webbrowser.open(flow.url), opened, lambda _: opened(False))

    def new_link(self):
        self.cancel_oauth()
        self.start_oauth(open_browser=False)

    def copy_link(self):
        if not self.flow:
            self.start_oauth(open_browser=False)
        self.window.clipboard_clear()
        self.window.clipboard_append(self.flow.url)
        self.status.set('Login link copied. Do not share the callback URL or tokens.')

    def submit_callback(self):
        if not self.flow:
            raise ValueError('Start a browser login first.')
        callback = self.callback.get()
        self.callback.set('')
        self.flow.submit(callback)
        self.status.set('Callback received. Finishing OAuth…')

    def cancel_oauth(self):
        flow, self.flow = self.flow, None
        if flow:
            flow.close()
        self.callback.set('')
        self.login_url.set('')
        self.status.set('Login cancelled; no account saved from this login.')

    def open_bulk(self):
        self.require_idle()
        self.close()
        self.app.bulk_add()

    def close(self):
        self.cancel_oauth()
        self.json_input.delete('1.0', 'end')
        self.window.destroy()
