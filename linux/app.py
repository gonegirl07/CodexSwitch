#!/usr/bin/env python3
"""Codex CLI Hub desktop for Linux."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import hub


class PoolPicker:
    def __init__(self, app, index):
        import pools
        self.app, self.index = app, index
        self.window = tk.Toplevel(app.window)
        self.window.title(f'Add/Remove · Pool {index + 1}')
        self.window.transient(app.window)
        self.window.geometry('980x560')
        self.member_ids = pools.load_config(app.store.root)['pools'][index]['members'][:]
        lists = ttk.Frame(self.window, padding=18)
        lists.pack(fill='both', expand=True)
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(2, weight=1)
        lists.rowconfigure(3, weight=1)
        ttk.Label(lists, text='Available accounts (not in this pool)').grid(row=0, column=0, sticky='w')
        ttk.Label(lists, text='Accounts in this pool').grid(row=0, column=2, sticky='w')
        self.available = tk.Listbox(lists, selectmode='extended', exportselection=False, height=14)
        self.members = tk.Listbox(lists, selectmode='extended', exportselection=False, height=14)
        self.available.grid(row=3, column=0, sticky='nsew')
        self.members.grid(row=3, column=2, sticky='nsew')
        moves = ttk.Frame(lists, padding=8)
        moves.grid(row=3, column=1)
        self.add_button = ttk.Button(moves, text='Add →', command=lambda: app.guard(lambda: self.move(True)))
        self.remove_button = ttk.Button(moves, text='← Remove', command=lambda: app.guard(lambda: self.move(False)))
        self.add_button.pack(pady=8)
        self.remove_button.pack(pady=8)
        self.filters, self.available_ids, self.visible_member_ids = {}, [], []
        self.select_all_available = self.select_all_members = None
        for side, column in (('available', 0), ('members', 2)):
            controls = ttk.Frame(lists)
            controls.grid(row=1, column=column, sticky='ew', pady=6)
            query, tier, count = tk.StringVar(), tk.StringVar(value='All'), tk.StringVar()
            ttk.Label(controls, text='Search email').pack(anchor='w')
            ttk.Entry(controls, textvariable=query).pack(fill='x')
            tabs = ttk.Frame(controls)
            tabs.pack(fill='x', pady=4)
            for label in ('All', 'Plus', 'Free', 'Other'):
                ttk.Radiobutton(tabs, text=label, value=label, variable=tier, style='Toolbutton').pack(side='left')
            ttk.Label(controls, textvariable=count).pack(anchor='w')
            select = ttk.Button(controls, text='Select all', command=lambda value=side: self.select_all(value))
            select.pack(anchor='w', pady=(4, 0))
            if side == 'available':
                self.select_all_available = select
            else:
                self.select_all_members = select
            self.filters[side] = {'query': query, 'tier': tier, 'count': count}
            query.trace_add('write', lambda *_: self.refresh())
            tier.trace_add('write', lambda *_: self.refresh())
        ttk.Button(self.window, text='Done', command=self.window.destroy).pack(anchor='e', padx=18, pady=(0, 12))
        self.refresh()

    def select_all(self, side):
        widget = self.available if side == 'available' else self.members
        widget.selection_set(0, 'end')

    def refresh(self):
        accounts = {account['id']: account for account in self.app.store.accounts}
        for side in ('available', 'members'):
            widget = self.available if side == 'available' else self.members
            id_key = 'available_ids' if side == 'available' else 'visible_member_ids'
            old = getattr(self, id_key)
            selected = {old[i] for i in widget.curselection() if i < len(old)}
            scroll = widget.yview()[0]
            identities = [i for i in accounts if i not in self.member_ids] if side == 'available' else self.member_ids
            filters = self.filters[side]
            query, tier = filters['query'].get().strip().casefold(), filters['tier'].get()
            visible = []
            for identity in identities:
                account = accounts.get(identity, {})
                name = account.get('email') or account.get('name', 'Removed account · '+identity[:8])
                plan = account.get('subscription', 'Unknown')
                matches = tier == 'All' or plan == tier or (tier == 'Other' and plan not in ('Free', 'Plus'))
                if matches and query in name.casefold():
                    visible.append((identity, f'{name}  [{plan}]'))
            setattr(self, id_key, [identity for identity, name in visible])
            widget.delete(0, 'end')
            for index, (identity, name) in enumerate(visible):
                widget.insert('end', name)
                if identity in selected:
                    widget.selection_set(index)
            widget.yview_moveto(scroll)
            filters['count'].set(f'{len(visible)}/{len(identities)} accounts')

    def move(self, add):
        import pools
        widget, identities = (self.available, self.available_ids) if add else (self.members, self.visible_member_ids)
        selected = [identities[i] for i in widget.curselection()]
        if not selected:
            return
        if add:
            self.app.apply_pool_members(self.index, add=selected)
        else:
            self.app.apply_pool_members(self.index, remove=selected)
        self.member_ids = pools.load_config(self.app.store.root)['pools'][self.index]['members'][:]
        self.refresh()


class Application:
    def __init__(self, window, store):
        self.window, self.store = window, store
        self.jobs = queue.Queue()
        self.busy = set()
        self.terminals = {}
        self.bulk_dialog = None
        self.pool_dialog = None
        self.add_dialog = None
        self.close_requested = False
        self.quota_due = {}
        self.relogin_busy = set()
        self.relogin_due = {}
        self.active_tier = tk.StringVar(value='All')
        self.tab_scroll = {}
        self.groups = {}
        self.current_account_id = None
        self.pool_activity = {}
        changed = False
        for account in store.accounts:
            before = dict(account)
            try:
                store.sync_identity(account)
            except (OSError, ValueError):
                email = store.identity_email(account)
                account.update(name=email or account.get('name') or 'Email unavailable', email=email)
            changed |= before != account
        if changed:
            store.save_accounts()
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.title('Codex CLI Hub · Linux')
        window.geometry('1180x720')
        window.minsize(900, 560)
        window.configure(bg='#0f1720')
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('.', font=('DejaVu Sans', 10))
        style.configure('TFrame', background='#0f1720')
        style.configure('TLabel', background='#0f1720', foreground='#e5e7eb')
        style.configure('Title.TLabel', font=('DejaVu Sans', 22, 'bold'), foreground='#f8fafc')
        style.configure('Muted.TLabel', foreground='#9aa7b5')
        style.configure('Summary.TFrame', background='#141f2b')
        style.configure('Summary.TLabel', background='#141f2b', foreground='#cbd5e1')
        style.configure('Card.TFrame', background='#17212e')
        style.configure('Card.TLabel', background='#17212e', foreground='#e5e7eb')
        style.configure('Panel.TFrame', background='#121b26')
        style.configure('Panel.TLabel', background='#121b26', foreground='#dbe4ef')
        style.configure('Name.TLabel', background='#17212e', foreground='#78f0c4', font=('DejaVu Sans', 13, 'bold'))
        style.configure('PanelName.TLabel', background='#121b26', foreground='#f8fafc', font=('DejaVu Sans', 17, 'bold'))
        style.configure('Small.TLabel', background='#17212e', foreground='#aeb9c7')
        style.configure('Tag.TLabel', background='#26394d', foreground='#c7e5ff', font=('DejaVu Sans', 9, 'bold'))
        style.configure('Activity.TLabel', background='#17212e', foreground='#78f0c4', font=('DejaVu Sans', 9))
        style.configure('Accent.TButton', padding=(14, 8), background='#2563eb', foreground='#f8fafc')
        style.map('Accent.TButton', background=[('active', '#1d4ed8'), ('disabled', '#202938')])
        style.configure('TButton', padding=(12, 8), background='#273449', foreground='#f8fafc')
        style.map('TButton', background=[('active', '#334155'), ('disabled', '#202938')])
        style.configure('Compact.TButton', padding=(2, 4), background='#273449', foreground='#f8fafc')
        style.map('Compact.TButton', background=[('active', '#334155'), ('disabled', '#202938')])
        style.configure('CompactAccent.TButton', padding=(2, 4), background='#2563eb', foreground='#f8fafc')
        style.map('CompactAccent.TButton', background=[('active', '#1d4ed8'), ('disabled', '#202938')])
        style.configure('TEntry', fieldbackground='#f8fafc', foreground='#111827', padding=6)
        style.configure('Horizontal.TProgressbar', troughcolor='#334155', background='#55d66f', bordercolor='#334155', lightcolor='#55d66f', darkcolor='#55d66f')
        top = ttk.Frame(window, padding=(24, 18, 24, 10))
        top.pack(fill='x')
        title = ttk.Frame(top)
        title.pack(side='left')
        ttk.Label(title, text='Codex CLI Hub', style='Title.TLabel').pack(anchor='w')
        self.summary = tk.StringVar(value='0 accounts · Manual refresh')
        ttk.Label(title, textvariable=self.summary, style='Muted.TLabel').pack(anchor='w', pady=(3, 0))
        for label, operation, style_name in [('Settings', self.settings, 'TButton'), ('Pools', self.pool_settings, 'TButton'), ('Bulk', self.bulk_add, 'TButton'), ('+ Add', self.add, 'Accent.TButton')]:
            ttk.Button(top, text=label, style=style_name, command=lambda fn=operation: self.guard(fn)).pack(side='right', padx=(10, 0))
        strip = ttk.Frame(window, style='Summary.TFrame', padding=(24, 12))
        strip.pack(fill='x')
        self.strip_status = tk.StringVar(value='Ready')
        ttk.Label(strip, textvariable=self.strip_status, style='Summary.TLabel').pack(side='left')
        self.strip_counts = tk.StringVar(value='')
        ttk.Label(strip, textvariable=self.strip_counts, style='Summary.TLabel').pack(side='right')
        container = ttk.Frame(window, padding=(24, 16, 24, 0))
        container.pack(fill='both', expand=True)
        self.container = container
        container.columnconfigure(0, weight=3)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1)
        list_panel = ttk.Frame(container)
        self.list_panel = list_panel
        list_panel.grid(row=0, column=0, sticky='nsew')
        tier_strip = ttk.Frame(list_panel)
        self.tier_strip = tier_strip
        tier_strip.pack(fill='x', pady=(0, 8))
        self.tier_canvas = tk.Canvas(tier_strip, bg='#0f1720', highlightthickness=0, height=30)
        self.tier_canvas.pack(fill='x')
        tier_scroll = ttk.Scrollbar(tier_strip, orient='horizontal', command=self.tier_canvas.xview)
        tier_scroll.pack(fill='x')
        self.tier_canvas.configure(xscrollcommand=tier_scroll.set)
        self.tier_tabs = ttk.Frame(self.tier_canvas)
        self.tier_canvas.create_window((0, 0), window=self.tier_tabs, anchor='nw')
        self.tier_tabs.bind('<Configure>', lambda event: self.tier_canvas.configure(
            scrollregion=self.tier_canvas.bbox('all'), height=event.height))
        self.add_pool_button = ttk.Button(self.tier_tabs, text='+', width=2, style='Compact.TButton',
                                          command=lambda: self.guard(self.create_hub_pool))
        self.delete_pool_button = ttk.Button(self.tier_tabs, text='−', width=2, style='Compact.TButton',
                                             command=lambda: self.guard(self.delete_hub_pool))
        self.pool_picker = None
        self.filter_bar = ttk.Frame(list_panel)
        self.filter_bar.pack(fill='x', pady=(0, 8))
        self.filter_query = tk.StringVar()
        self.filter_quota = tk.StringVar(value='All')
        ttk.Label(self.filter_bar, text='Search').pack(side='left')
        ttk.Entry(self.filter_bar, textvariable=self.filter_query, width=22).pack(side='left', padx=(8, 16))
        for label in ('All', 'Remaining', 'Exhausted'):
            ttk.Radiobutton(self.filter_bar, text=label, value=label, variable=self.filter_quota, style='Toolbutton').pack(side='left')
        self.pool_bar = ttk.Frame(list_panel)
        self.pool_bar_status = tk.StringVar(value='')
        ttk.Label(self.pool_bar, textvariable=self.pool_bar_status, style='Muted.TLabel').pack(side='left')
        self.pool_toggle = ttk.Button(self.pool_bar, text='Add/Remove', command=lambda: self.guard(self.open_pool_picker))
        self.pool_toggle.pack(side='right')
        self.canvas = tk.Canvas(list_panel, bg='#0f1720', highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_panel, orient='vertical', command=self.canvas.yview)
        self.cards = ttk.Frame(self.canvas)
        self.card_window = self.canvas.create_window((0, 0), window=self.cards, anchor='nw')
        self.cards.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.card_window, width=event.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.detail = ttk.Frame(container, style='Panel.TFrame', padding=18)
        self.detail.grid(row=0, column=1, sticky='nsew')
        self.detail.configure(width=410)
        detail_header = ttk.Frame(self.detail, style='Panel.TFrame')
        detail_header.pack(fill='x')
        self.detail_title = ttk.Label(detail_header, text='', style='PanelName.TLabel', wraplength=300)
        self.detail_title.pack(side='left', anchor='w', fill='x', expand=True)
        self.detail_close = ttk.Button(detail_header, text='X', width=3, command=self.close_detail)
        self.detail_close.pack(side='right')
        self.detail_canvas = tk.Canvas(self.detail, bg='#121b26', highlightthickness=0)
        detail_scroll = ttk.Scrollbar(self.detail, command=self.detail_canvas.yview)
        detail_scroll.pack(side='right', fill='y')
        self.detail_canvas.pack(fill='both', expand=True, pady=(18, 0))
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        self.detail_body = ttk.Frame(self.detail_canvas, style='Panel.TFrame')
        detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_body, anchor='nw')
        self.detail_body.bind('<Configure>', lambda event: self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox('all')))
        self.detail_canvas.bind('<Configure>', lambda event: self.detail_canvas.itemconfigure(detail_window, width=event.width))
        window.bind('<Button-4>', lambda event: self.scroll_accounts(event, -3))
        window.bind('<Button-5>', lambda event: self.scroll_accounts(event, 3))
        self.status = tk.StringVar(value=f'Data: {store.root}')
        ttk.Label(window, textvariable=self.status, style='Muted.TLabel', padding=(24, 12), wraplength=1080).pack(fill='x')
        self.selected_account_id = None
        self.detail_visible = False
        self.account_rows = []
        self.priority_header = None
        self.header_actions = None
        self.filter_query.trace_add('write', lambda *_: self.layout_groups())
        self.filter_quota.trace_add('write', lambda *_: self.layout_groups())
        self.hide_detail()
        self.render()
        self.update_activity()
        window.after(100, self.poll)
        window.after(1000, self.auto_refresh)

    def guard(self, operation):
        try:
            return operation()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            messagebox.showerror('Codex CLI Hub', str(error), parent=self.window)
            self.status.set('Operation failed. See the error message.')

    def render(self):
        for widget in self.cards.winfo_children():
            widget.destroy()
        self.account_rows = []
        healthy = sum(1 for account in self.store.accounts if 'Logged in' in account.get('status', ''))
        warnings = len(self.store.accounts) - healthy
        account_word = 'account' if len(self.store.accounts) == 1 else 'accounts'
        self.summary.set(f'{len(self.store.accounts)} {account_word} · {healthy} healthy · {warnings} warning')
        self.strip_status.set('● Auto quota ~30s · Up to 2 concurrent checks · Pools in Pools panel')
        self.strip_counts.set('')
        if not self.store.accounts:
            self.layout_groups()
            empty = ttk.Frame(self.cards, padding=36, style='Card.TFrame')
            empty.pack(fill='x', pady=12)
            ttk.Label(empty, text='Your accounts, in one place', style='Name.TLabel').pack(anchor='w')
            ttk.Label(empty, text='Add an account to log in with Codex. Each profile keeps its own authentication and configuration.', style='Card.TLabel', padding=(0, 16), wraplength=700).pack(anchor='w')
            ttk.Button(empty, text='+ Add your first account', style='Accent.TButton', command=lambda: self.guard(self.add)).pack(anchor='w')
            self.render_detail(None)
            self.hide_detail()
            return
        if not any(account['id'] == self.selected_account_id for account in self.store.accounts):
            self.selected_account_id = self.store.accounts[0]['id'] if self.detail_visible else None
        header = ttk.Frame(self.cards, style='Summary.TFrame', padding=(14, 8))
        header.pack(fill='x', pady=(0, 8))
        self.header_actions = None
        self.priority_header = ttk.Label(header, text='Priority', width=5, style='Summary.TLabel')
        for column, (text, width) in enumerate([('Account', 16), ('State', 11), ('Expires', 5), ('Checked', 8), ('Quota', 8), ('', 8), ('Actions', 16)]):
            header.columnconfigure(column, weight=1 if column == 0 else 0)
            label = ttk.Label(header, text=text, width=width, style='Summary.TLabel')
            label.grid(row=0, column=column, sticky='w', padx=(0, 8))
            if text == 'Actions':
                self.header_actions = label
        for account in self.store.accounts:
            selected = account['id'] == self.selected_account_id
            card = ttk.Frame(self.cards, style='Card.TFrame', padding=(10, 8))
            card.pack(fill='x', pady=(0, 8))
            for column, minimum in enumerate([132, 124, 36, 76, 56, 56, 196]):
                card.columnconfigure(column, weight=1 if column == 0 else 0, minsize=minimum)
            card.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            name = ttk.Label(card, text=self.display_name(account), style='Name.TLabel', width=18, wraplength=0, justify='left')
            name.grid(row=0, column=0, sticky='w', padx=(0, 8))
            name.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            tier = account.get('subscription', 'Unknown')
            state = ttk.Frame(card, style='Card.TFrame', width=132, height=24)
            state.grid(row=0, column=1, sticky='w', padx=(0, 8))
            state.grid_propagate(False)
            state.pack_propagate(False)
            state.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            subscription = ttk.Label(state, text=self.display_tag(tier), style='Tag.TLabel', width=6, padding=(4, 1))
            subscription.pack(side='left')
            subscription.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            error = ttk.Label(state, text=self.error_chip(account), style='Tag.TLabel', width=4, padding=(2, 1))
            if self.error_chip(account):
                error.pack(side='left', padx=(4, 0))
            error.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            activity = ttk.Label(state, text=self.activity_text(account), style='Activity.TLabel', width=10)
            activity.pack(side='left', padx=(5, 0))
            activity.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            expiry = ttk.Label(card, text=hub.subscription_remaining(account), style='Small.TLabel', width=5)
            expiry.grid(row=0, column=2, sticky='w', padx=(0, 8))
            expiry.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            checked = ttk.Label(card, text=self.checked_time(account), style='Small.TLabel', width=11)
            checked.grid(row=0, column=3, sticky='w', padx=(0, 8))
            checked.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            status = 'Refreshing…' if account['id'] in self.busy else account.get('status', 'Not checked')
            quota_5h, quota_weekly = self.quota_values(account)
            quota_5h_cell = ttk.Frame(card, style='Card.TFrame', width=136, height=26)
            quota_5h_cell.pack_propagate(False)
            quota_5h_cell.grid(row=0, column=4, columnspan=2, sticky='w', padx=(0, 8))
            quota_5h_cell.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            quota_5h_label = self.quota_bar(quota_5h_cell, *(quota_5h or ('', 0)))
            quota_5h_label.compact = True
            quota_5h_label.configure(width=64, height=24)
            quota_5h_label.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            quota_weekly_label = self.quota_bar(quota_5h_cell, *(quota_weekly or ('', 0)))
            quota_weekly_label.compact = True
            quota_weekly_label.configure(width=64, height=24)
            quota_weekly_label.bind('<Button-1>', lambda event, a=account: self.select_account(a))
            actions = ttk.Frame(card, style='Card.TFrame', width=196, height=26)
            actions.grid(row=0, column=6, sticky='e')
            actions.grid_propagate(False)
            actions.pack_propagate(False)
            action_labels = []
            reset_button = None
            for label, operation, style_name, width in [('Open', lambda a=account: self.open(a), 'CompactAccent.TButton', 4), ('Switch', lambda a=account: self.apply(a), 'Compact.TButton', 6), (self.reset_text(account), lambda a=account: self.reset_account(a), 'Compact.TButton', 8), ('...', lambda a=account: self.more(a), 'Compact.TButton', 2)]:
                button = ttk.Button(actions, text=label, width=width, style=style_name, command=lambda fn=operation: self.guard(fn))
                button.pack(side='left', padx=(0, 1))
                action_labels.append(label)
                if label.startswith('Reset'):
                    reset_button = button
            priority_var = tk.StringVar(value=str(hub.account_priority_value(account)))
            priority = tk.Spinbox(card, from_=0, to=4, increment=1, width=3, wrap=False, textvariable=priority_var,
                                  state='readonly', font=('DejaVu Sans', 10),
                                  command=lambda a=account, v=priority_var: self.set_account_priority(a, v.get()))
            self.account_rows.append({'id': account['id'], 'frame': card, 'name': name, 'quota_5h': quota_5h_label, 'quota_weekly': quota_weekly_label,
                                      'subscription': subscription, 'tier': tier, 'activity': activity, 'error': error, 'expiry': expiry, 'checked': checked,
                                      'priority': priority, 'priority_var': priority_var, 'actions_frame': actions,
                                      'reset': reset_button, 'actions': action_labels})
            self.update_quota_slots((quota_5h_label, quota_weekly_label), (quota_5h, quota_weekly))
        self.layout_groups()
        selected_account = next((account for account in self.store.accounts if account['id'] == self.selected_account_id), None)
        self.render_detail(selected_account)
        if self.detail_visible and selected_account:
            self.show_detail()
        else:
            self.hide_detail()

    def select_account(self, account):
        self.selected_account_id = account['id']
        self.render_detail(account)
        self.show_detail()
        self.update_pool_toggle()

    def scroll_accounts(self, event, amount):
        canvas = self.detail_canvas if str(event.widget).startswith(str(self.detail)) else self.canvas
        canvas.yview_scroll(amount, 'units')

    def pool_tabs(self):
        try:
            import pools
            return [f'Pool {i}' for i in range(1, len(pools.load_config(self.store.root)['pools']) + 1)]
        except (OSError, ValueError):
            return []

    def active_pool_index(self):
        match = re.fullmatch(r'Pool (\d+)', self.active_tier.get())
        return int(match[1]) - 1 if match else None

    def reset_text(self, account):
        count = hub.reset_count(account.get('quota', []))
        return f"Reset({count if count is not None else '?'})"

    def layout_groups(self):
        scroll = self.canvas.yview()[0]
        grouped = {'All': self.account_rows[:]}
        members_by_pool = {}
        try:
            import pools
            for i, policy in enumerate(pools.load_config(self.store.root)['pools'], 1):
                members_by_pool[f'Pool {i}'] = set(policy['members'])
                grouped.setdefault(f'Pool {i}', [])
        except (OSError, ValueError):
            pass
        for row in self.account_rows:
            grouped.setdefault(row['tier'], []).append(row)
            for name, members in members_by_pool.items():
                if row['id'] in members:
                    grouped.setdefault(name, []).append(row)
            row['frame'].pack_forget()
        for tier, header in list(self.groups.items()):
            header.pack_forget()
            if tier not in grouped:
                header.destroy()
                del self.groups[tier]
        if self.active_tier.get() not in grouped:
            self.active_tier.set('All')
            scroll = self.tab_scroll.get('All', 0)
        order = ['All'] + self.pool_tabs() + ['Free', 'Go', 'Plus', 'Pro Lite', 'Pro', 'Pro5x', 'Pro20x', 'Business', 'Enterprise', 'Edu', 'Edu Plus', 'Edu Pro', 'Unknown']
        accounts = {account['id']: account for account in self.store.accounts}
        pool_index = self.active_pool_index()
        if pool_index is None:
            self.pool_bar.pack_forget()
        else:
            if not self.pool_bar.winfo_manager():
                self.pool_bar.pack(fill='x', pady=(0, 8), after=self.filter_bar)
            self.pool_bar_status.set(f'Pool {pool_index + 1} members.')
        self.add_pool_button.pack_forget()
        self.delete_pool_button.pack_forget()
        last_pool_tab = None
        for tier in sorted(grouped, key=lambda value: (order.index(value) if value in order else len(order), value)):
            if tier not in self.groups:
                self.groups[tier] = ttk.Button(self.tier_tabs, command=lambda value=tier: self.select_tier(value), style='Compact.TButton')
            self.groups[tier].configure(text=f'{tier} ({len(grouped[tier])})', style='CompactAccent.TButton' if tier == self.active_tier.get() else 'Compact.TButton')
            self.groups[tier].pack(side='left', padx=(0, 4))
            if re.fullmatch(r'Pool \d+', tier):
                last_pool_tab = self.groups[tier]
            if tier == self.active_tier.get():
                if pool_index is None:
                    ordered = sorted(grouped[tier], key=self.account_priority)
                else:
                    ordered = sorted(grouped[tier], key=lambda row: (hub.account_priority_value(accounts.get(row['id'], {})), self.account_priority(row)))
                for row in ordered:
                    if self.row_matches_filters(accounts.get(row['id'], {})):
                        row['frame'].pack(fill='x', pady=(0, 8))
        if last_pool_tab is not None:
            self.add_pool_button.pack(side='left', padx=(0, 4), after=last_pool_tab)
            self.delete_pool_button.pack(side='left', padx=(0, 4), after=self.add_pool_button)
        else:
            self.add_pool_button.pack_forget()
            self.delete_pool_button.pack_forget()
        self.apply_priority_column(pool_index is not None)
        self.update_pool_toggle()
        self.window.update_idletasks()
        self.canvas.yview_moveto(scroll)

    def select_tier(self, tier):
        if tier == self.active_tier.get() or tier not in self.groups:
            return
        self.tab_scroll[self.active_tier.get()] = self.canvas.yview()[0]
        self.active_tier.set(tier)
        self.layout_groups()
        self.canvas.yview_moveto(self.tab_scroll.get(tier, 0))

    def activity_text(self, account):
        tags = ['current'] if account['id'] == self.current_account_id else []
        return ' · '.join(tags + self.pool_activity.get(account['id'], []))

    def error_chip(self, account):
        if not account.get('quota_error'):
            return ''
        detail = hub.error_detail(account)
        if detail.get('http'):
            return str(detail['http'])
        return str(detail.get('code') or '')[:8]

    def error_text(self, account):
        if not account.get('quota_error'):
            return ''
        label = hub.format_error_label(account)
        raw = str(account['quota_error'])
        if label:
            return raw if raw.startswith(label) else f'{label} — {raw}'
        return 'Quota stale / unavailable: ' + raw

    def account_priority(self, account):
        return 0 if account['id'] == self.current_account_id else 1 if self.pool_activity.get(account['id']) else 2

    def update_subscription_time(self, account):
        text = hub.subscription_remaining(account)
        for row in self.account_rows:
            if row['id'] == account['id']:
                if row['expiry'].cget('text') != text:
                    row['expiry'].configure(text=text)
                checked = self.checked_time(account)
                if row['checked'].cget('text') != checked:
                    row['checked'].configure(text=checked)
                break
        if self.selected_account_id == account['id'] and self.detail_quota:
            if self.detail_expiry.cget('text') != text:
                self.detail_expiry.configure(text=text)

    def update_activity(self):
        from local_switch import current_account, pool_activity
        previous = [self.account_priority(row) for row in self.account_rows]
        self.current_account_id = current_account(self.store)
        self.pool_activity = pool_activity(self.store)
        for row in self.account_rows:
            account = next((account for account in self.store.accounts if account['id'] == row['id']), row)
            text = self.activity_text(account)
            if row['activity'].cget('text') != text:
                row['activity'].configure(text=text)
            checked = self.checked_time(account)
            if row['checked'].cget('text') != checked:
                row['checked'].configure(text=checked)
        if previous != [self.account_priority(row) for row in self.account_rows]:
            self.layout_groups()
        for account in self.store.accounts:
            self.update_subscription_time(account)
        self.update_pool_actions()
        self.update_pool_toggle()

    def show_detail(self):
        self.detail_visible = True
        self.container.columnconfigure(0, weight=3)
        self.container.columnconfigure(1, weight=1)
        self.list_panel.grid_configure(padx=(0, 14))
        self.detail.grid(row=0, column=1, sticky='nsew')

    def hide_detail(self):
        self.detail_visible = False
        self.detail.grid_remove()
        self.container.columnconfigure(0, weight=1)
        self.container.columnconfigure(1, weight=0)
        self.list_panel.grid_configure(padx=(0, 0))

    def close_detail(self):
        self.detail_visible = False
        self.selected_account_id = None
        self.hide_detail()

    def display_name(self, account):
        name = account.get('email') or account.get('name') or ''
        return name if len(name) <= 18 else name[:15] + '...'

    def display_tag(self, value):
        value = value or 'Unknown'
        return value if len(value) <= 6 else value[:3] + '...'

    def checked_time(self, account):
        if account.get('checked_at'):
            return time.strftime('%d/%m %H:%M', time.localtime(account['checked_at']))
        return 'not checked'

    def quota_exhausted(self, account):
        if account.get('quota_error'):
            return True
        values = [item for item in self.quota_values(account) if item is not None]
        return not values or all(item[1] <= 0 for item in values)

    def account_matches_filters(self, account, query=None, quota=None):
        query = (self.filter_query.get() if query is None else query).strip().casefold()
        name = str(account.get('email') or account.get('name') or '')
        if query and query not in name.casefold():
            return False
        quota = self.filter_quota.get() if quota is None else quota
        if quota == 'Remaining':
            return not self.quota_exhausted(account)
        if quota == 'Exhausted':
            return self.quota_exhausted(account)
        return True

    def row_matches_filters(self, account):
        return self.account_matches_filters(account)

    def apply_priority_column(self, show):
        header = getattr(self, 'priority_header', None)
        if header is None:
            return
        if show:
            header.grid(row=0, column=6, sticky='w', padx=(0, 8))
            if self.header_actions:
                self.header_actions.grid(row=0, column=7, sticky='w', padx=(0, 8))
        else:
            header.grid_remove()
            if self.header_actions:
                self.header_actions.grid(row=0, column=6, sticky='w', padx=(0, 8))
        for row in self.account_rows:
            spin, actions = row.get('priority'), row.get('actions_frame')
            if spin is None:
                continue
            if show:
                spin.grid(row=0, column=6, sticky='w', padx=(0, 8))
                if actions:
                    actions.grid(row=0, column=7, sticky='e')
            else:
                spin.grid_remove()
                if actions:
                    actions.grid(row=0, column=6, sticky='e')

    def set_account_priority(self, account, value):
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            parsed = 1
        parsed = hub.account_priority_value({'priority': parsed})
        if account.get('priority') == parsed:
            return
        account['priority'] = parsed
        self.store.save_accounts()
        text = str(parsed)
        for row in self.account_rows:
            if row['id'] == account['id'] and row.get('priority_var') and row['priority_var'].get() != text:
                row['priority_var'].set(text)

    def quota_values(self, account, detail=False):
        values = []
        for window in hub.account_quota_windows(account)[:2]:
            label = window['label']
            if label == 'Weekly' and not detail:
                label = 'W'
            value = window['remaining']
            text = f'{label} {value:g}%' if value is not None else f'{label} --'
            if account.get('quota_error'):
                text += ' · stale'
            values.append((text, value if value is not None else 0))
        if not values:
            values.append(('Quota --' + (' · stale' if account.get('quota_error') else ''), 0))
        return (values + [None, None])[:2]

    def update_quota_slots(self, canvases, values):
        # Fixed-size containers keep the layout stable when a window appears/disappears.
        for canvas, value in zip(canvases, values):
            if value is None:
                if canvas.winfo_manager():
                    canvas.pack_forget()
            else:
                compact = getattr(canvas, 'compact', False)
                if compact:
                    width = 66 if all(v is not None for v in values) else 136
                    if int(canvas.cget('width')) != width:
                        canvas.configure(width=width)
                self.update_quota_bar(canvas, *value)
                if not canvas.winfo_manager():
                    canvas.pack(side='left' if compact else 'top', fill='none' if compact else 'x')

    def quota_summary(self, account, needle, label, bucket='codex'):
        value = hub.quota_remaining(account.get('quota', []), needle, bucket)
        if value is not None:
            return f'{label} {value:g}%' + (' · stale' if account.get('quota_error') else ''), value
        if account.get('quota_error'):
            return f'{label} stale', 0
        return f'{label} --', 0

    def quota_bar(self, parent, text, value):
        canvas = tk.Canvas(parent, width=175, height=26, bg='#334155', highlightthickness=0)
        canvas.quota_state = (text, value)
        canvas.create_rectangle(0, 0, 0, 26, outline='', tags='fill')
        canvas.create_text(0, 14, fill='#111827', font=('DejaVu Sans', 10, 'bold'), tags='shadow')
        canvas.create_text(0, 13, fill='#ffffff', font=('DejaVu Sans', 10, 'bold'), tags='label')
        def draw(event=None):
            width = max(1, canvas.winfo_width())
            canvas.coords('fill', 0, 0, width*max(0, min(100, canvas.quota_state[1]))/100, 26)
            canvas.coords('shadow', width/2+1, 14)
            canvas.coords('label', width/2, 13)
        canvas.bind('<Configure>', draw)
        canvas.itemconfigure('fill', fill='#55d66f' if 'stale' not in text else '#64748b')
        canvas.itemconfigure('shadow', text=text)
        canvas.itemconfigure('label', text=text)
        draw()
        return canvas

    def update_quota_bar(self, canvas, text, value):
        if canvas.quota_state == (text, value):
            return
        canvas.quota_state = (text, value)
        canvas.coords('fill', 0, 0, max(1, canvas.winfo_width())*max(0, min(100, value))/100, 26)
        canvas.itemconfigure('fill', fill='#64748b' if 'stale' in text else '#55d66f')
        canvas.itemconfigure('shadow', text=text)
        canvas.itemconfigure('label', text=text)

    def update_account_quota(self, account):
        self.update_subscription_time(account)
        values = self.quota_values(account)
        for row in self.account_rows:
            if row['id'] == account['id']:
                tier = account.get('subscription', 'Unknown')
                if row['tier'] != tier:
                    row['tier'] = tier
                    row['subscription'].configure(text=self.display_tag(tier))
                    self.layout_groups()
                self.update_quota_slots((row['quota_5h'], row['quota_weekly']), values)
                if row['name'].cget('text') != self.display_name(account):
                    row['name'].configure(text=self.display_name(account))
                if row.get('reset'):
                    text = self.reset_text(account)
                    if row['reset'].cget('text') != text:
                        row['reset'].configure(text=text)
                if row.get('priority_var'):
                    text = str(hub.account_priority_value(account))
                    if row['priority_var'].get() != text:
                        row['priority_var'].set(text)
                if row.get('error'):
                    chip = self.error_chip(account)
                    if row['error'].cget('text') != chip:
                        row['error'].configure(text=chip)
                    if chip and not row['error'].winfo_manager():
                        row['error'].pack(side='left', padx=(4, 0), after=row['subscription'])
                    elif not chip and row['error'].winfo_manager():
                        row['error'].pack_forget()
                break
        if self.selected_account_id == account['id'] and self.detail_quota:
            self.update_quota_slots(self.detail_quota, self.quota_values(account, detail=True))
            windows = hub.account_quota_windows(account)
            for index, label in enumerate(self.detail_resets):
                text = ''
                if index < len(windows):
                    reset = windows[index].get('resets_at')
                    text = 'Reset: unknown'
                    if type(reset) in (int, float):
                        try:
                            text = time.strftime('Reset: %d/%m %H:%M', time.localtime(reset))
                        except (ValueError, OverflowError, OSError):
                            pass
                if label.cget('text') != text:
                    label.configure(text=text)
            reserve = hub.quota_remaining(account.get('quota', []), 'Weekly', 'gpt-reserve')
            if reserve is not None:
                self.update_quota_bar(self.detail_reserve, *self.quota_summary(account, 'Weekly', 'GPT Reserve weekly', 'gpt-reserve'))
                if not self.detail_reserve.winfo_manager():
                    self.detail_reserve.pack(fill='x', pady=(18, 0), before=self.detail_checked)
            elif self.detail_reserve.winfo_manager():
                self.detail_reserve.pack_forget()
            badge = self.status_badge(account.get('status', 'Not checked'), account)
            if self.detail_badge.cget('text') != badge:
                self.detail_badge.configure(text=badge)
            tier = account.get('subscription', 'Unknown')
            if self.detail_subscription.cget('text') != tier:
                self.detail_subscription.configure(text=tier)
            checked = time.strftime('Quota checked: %d/%m %H:%M', time.localtime(account['checked_at'])) if account.get('checked_at') else ''
            if self.detail_checked.cget('text') != checked:
                self.detail_checked.configure(text=checked)
            error = self.error_text(account)
            if self.detail_error.cget('text') != error:
                self.detail_error.configure(text=error)
            self.update_pool_actions()

    def status_badge(self, status, account):
        if account.get('quota_error') or 'failed' in status.casefold():
            return '● Warning'
        if 'Logged in' in status:
            return '● Healthy'
        if 'Refreshing' in status:
            return '● Refreshing'
        return '● Pending'

    def render_detail(self, account):
        self.detail_quota = []
        self.detail_resets = []
        self.detail_actions = {}
        for widget in self.detail_body.winfo_children():
            widget.destroy()
        if not account:
            self.detail_title.configure(text='No account selected')
            ttk.Label(self.detail_body, text='Add an account to see quota, notes and actions here.', style='Panel.TLabel', wraplength=300).pack(anchor='w')
            return
        self.detail_title.configure(text=account.get('email') or account['name'])
        status = 'Refreshing…' if account['id'] in self.busy else account.get('status', 'Not checked')
        self.detail_badge = ttk.Label(self.detail_body, text=self.status_badge(status, account), style='Panel.TLabel')
        self.detail_badge.pack(anchor='w')
        self.detail_subscription = ttk.Label(self.detail_body, text=account.get('subscription', 'Unknown'), style='Panel.TLabel')
        self.detail_subscription.pack(anchor='w')
        ttk.Label(self.detail_body, text='Priority: '+str(hub.account_priority_value(account)), style='Panel.TLabel').pack(anchor='w')
        self.detail_expiry = ttk.Label(self.detail_body, text=hub.subscription_remaining(account), style='Panel.TLabel')
        self.detail_expiry.pack(anchor='w')
        if account.get('note'):
            ttk.Label(self.detail_body, text=account['note'], style='Panel.TLabel', wraplength=300, padding=(0, 12, 0, 0)).pack(anchor='w')
        for value in self.quota_values(account, detail=True):
            cell = ttk.Frame(self.detail_body, style='Panel.TFrame', height=46)
            cell.pack(fill='x', pady=(18, 0))
            cell.pack_propagate(False)
            canvas = self.quota_bar(cell, *(value or ('', 0)))
            reset_label = ttk.Label(cell, text='', style='Panel.TLabel')
            reset_label.pack(side='bottom', anchor='w')
            self.detail_resets.append(reset_label)
            self.detail_quota.append(canvas)
        self.detail_reserve = self.quota_bar(self.detail_body, '', 0)
        self.detail_checked = ttk.Label(self.detail_body, text='', style='Panel.TLabel', padding=(0, 18, 0, 0))
        self.detail_checked.pack(anchor='w')
        # Reserve a fixed error region: errors must not move the actions/layout.
        error_region = ttk.Frame(self.detail_body, style='Panel.TFrame', height=72)
        error_region.pack(fill='x', pady=(8, 0))
        error_region.pack_propagate(False)
        self.detail_error = ttk.Label(error_region, text='', style='Panel.TLabel', wraplength=280)
        self.detail_error.pack(anchor='w')
        self.update_account_quota(account)
        actions = ttk.Frame(self.detail_body, style='Panel.TFrame', padding=(0, 22, 0, 0))
        actions.pack(fill='x', side='bottom')
        for index, (key, label, operation) in enumerate(self.account_actions(account)):
            button = ttk.Button(actions, text=label, style='Compact.TButton', command=lambda fn=operation: self.guard(fn))
            button.grid(row=index//2, column=index%2, sticky='ew', padx=2, pady=3)
            self.detail_actions[key] = button
        actions.columnconfigure((0, 1), weight=1)
        self.detail_canvas.yview_moveto(0)

    def form(self, title, fields, options=None):
        dialog = tk.Toplevel(self.window)
        dialog.title(title)
        dialog.transient(self.window)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=24)
        body.pack(fill='both', expand=True)
        values, result = {}, {}
        for label, key, default in fields:
            ttk.Label(body, text=label).pack(anchor='w', pady=(8, 4))
            variable = tk.StringVar(value=default)
            ttk.Entry(body, textvariable=variable, width=62).pack(fill='x')
            values[key] = variable
        if options:
            variable = tk.StringVar(value=options[0])
            ttk.Label(body, text='Authentication').pack(anchor='w', pady=(16, 6))
            ttk.Combobox(body, textvariable=variable, values=options, state='readonly', width=50).pack(fill='x')
            values['mode'] = variable

        def submit():
            result.update({key: variable.get() for key, variable in values.items()})
            dialog.destroy()

        footer = ttk.Frame(body, padding=(0, 20, 0, 0))
        footer.pack(fill='x')
        ttk.Button(footer, text='Save' if options is None else 'Add account', command=submit).pack(side='right')
        ttk.Button(footer, text='Cancel', command=dialog.destroy).pack(side='right', padx=8)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result

    def add(self):
        from account_add_ui import AddDialog
        if self.add_dialog and self.add_dialog.window.winfo_exists():
            self.add_dialog.window.lift()
            return
        self.store.shared_target()
        self.add_dialog = AddDialog(self)

    def bulk_add(self):
        from bulk_ui import BulkDialog
        if self.bulk_dialog and self.bulk_dialog.window.winfo_exists():
            self.bulk_dialog.window.lift()
            return
        self.store.shared_target()
        self.bulk_dialog = BulkDialog(self)

    def pool_settings(self):
        from pool_ui import PoolDialog
        if self.pool_dialog and self.pool_dialog.window.winfo_exists():
            self.pool_dialog.window.lift()
            return
        self.pool_dialog = PoolDialog(self)

    def close(self):
        if self.add_dialog and self.add_dialog.window.winfo_exists():
            self.add_dialog.close()
        if self.bulk_dialog and self.bulk_dialog.running:
            self.close_requested = True
            self.bulk_dialog.stop()
            self.status.set('Stopping login browsers before closing…')
        else:
            self.window.destroy()

    def settings(self):
        settings = self.store.settings
        values = self.form('Settings', [('Default Codex home (contains sessions)', 'default_home', settings['default_home']), ('Default working directory', 'working_directory', settings['working_directory']), ('Codex executable name or absolute path', 'codex', settings['codex'])])
        if values:
            self.store.save_settings(dict(settings, **values))
            self.status.set('Settings saved.')

    def choose_project(self):
        dialog = tk.Toplevel(self.window)
        dialog.title('Choose project')
        dialog.geometry('800x500')
        dialog.transient(self.window)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill='both', expand=True)
        search = tk.StringVar()
        ttk.Entry(body, textvariable=search).pack(fill='x', pady=(0, 10))
        tree = ttk.Treeview(body, columns=('path',), show='headings', selectmode='browse')
        tree.heading('path', text='Project directory')
        tree.pack(fill='both', expand=True)
        info = ttk.Label(body, text='Reading session metadata…', style='Muted.TLabel')
        info.pack(anchor='w', pady=8)
        result, paths = [], []

        def populate(*unused):
            tree.delete(*tree.get_children())
            for path in paths:
                if search.get().casefold() in path.casefold():
                    tree.insert('', 'end', values=(path + ('' if Path(path).is_dir() else '  [missing]'),), tags=(path,))

        def select(path):
            if path and Path(path).is_dir():
                result.append(path)
                dialog.destroy()

        def selected():
            if tree.selection():
                select(tree.item(tree.selection()[0], 'tags')[0])

        def loaded(value):
            if not dialog.winfo_exists():
                return
            found, skipped = value
            paths.extend(found)
            default = self.store.settings['working_directory']
            if default not in paths:
                paths.insert(0, default)
            populate()
            info.configure(text=f'{len(paths)} directories · {skipped} unreadable/unsupported session files skipped')

        self.background(lambda: hub.projects(self.store.shared_target()), loaded)
        search.trace_add('write', populate)
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='Browse…', command=lambda: select(filedialog.askdirectory(parent=dialog, initialdir=self.store.settings['working_directory']))).pack(side='left')
        ttk.Button(buttons, text='Open', command=selected).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=8)
        tree.bind('<Double-1>', lambda event: selected())
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result[0] if result else None

    def launch(self, account, action, work=None, executable=None):
        profile = self.store.validate(account)
        self.store.ensure_config(profile)
        process = hub.launch(executable or hub.codex_path(self.store.settings), profile, work or self.store.settings['working_directory'], action)
        self.terminals.setdefault(account['id'], []).append(process)
        if action == 'login':
            def login_finished():
                if process.poll() is None:
                    self.window.after(500, login_finished)
                elif account in self.store.accounts:
                    def finish():
                        self.store.sync_identity(account)
                        self.store.save_accounts()
                        self.render()
                        if (profile / 'auth.json').is_file():
                            self.refresh(account)
                    self.guard(finish)
            self.window.after(500, login_finished)

    def open(self, account, action='open'):
        work = self.choose_project()
        if work:
            self.launch(account, action, work)
            self.status.set(f"Opened {account['name']} in {work}")

    def background(self, operation, success, failure=None):
        def run():
            try:
                self.jobs.put((success, operation()))
            except Exception as error:
                self.jobs.put((failure or self.show_background_error, str(error)))
        # Let bounded diagnostic workers finish cleanup if the window closes mid-request.
        threading.Thread(target=run, daemon=False).start()

    def show_background_error(self, error):
        messagebox.showerror('Codex CLI Hub', error, parent=self.window)

    def poll(self):
        while not self.jobs.empty():
            callback, result = self.jobs.get_nowait()
            self.guard(lambda: callback(result))
        self.window.after(100, self.poll)

    def refresh(self, account, reset_key=None):
        if account['id'] in self.busy:
            return
        profile = self.store.validate(account)
        self.store.ensure_config(profile)
        executable = hub.codex_path(self.store.settings)
        self.busy.add(account['id'])

        def complete(update):
            self.busy.discard(account['id'])
            self.quota_due[account['id']] = time.monotonic() + (60 if update.get('quota_error') else 30)
            if account not in self.store.accounts:
                return
            account.update(update)
            if reset_key is not None:
                outcome = update.get('reset_outcome')
                if outcome:
                    account.pop('reset_pending_key', None)
                messages = {'reset': 'Reset completed.', 'alreadyRedeemed': 'This reset was already completed.',
                            'nothingToReset': 'No quota window is eligible for reset.', 'noCredit': 'No reset credits available.'}
                self.status.set(account['name'] + ': ' + messages.get(outcome, 'Reset not confirmed; refresh before retrying.'))
            try:
                self.store.sync_identity(account)
                if update.get('subscription') and not update.get('quota_error'):
                    account['subscription'] = update['subscription']
                self.store.save_accounts()
            except (OSError, ValueError):
                account['quota_error'] = 'Quota updated in memory; account metadata could not be saved. Check local storage.'
                self.quota_due[account['id']] = time.monotonic() + 60
            finally:
                self.update_account_quota(account)
                if account in self.store.accounts:
                    self.maybe_relogin(account, update)

        operation = (lambda: hub.reset_quota(executable, profile, reset_key)) if reset_key is not None else (lambda: hub.refresh(executable, profile))
        self.background(operation, complete,
                        lambda error: complete({'quota_error': error, 'status': 'Check failed'}))

    def maybe_relogin(self, account, update):
        if not hub.login_required(update) or self.relogin_busy or account['id'] in self.busy:
            return
        if time.monotonic() < self.relogin_due.get(account['id'], 0):
            return
        credentials = self.store.load_login(account)
        if not credentials:
            return
        self.relogin_busy.add(account['id'])
        self.relogin_due[account['id']] = time.monotonic() + 600
        self.status.set((account.get('email') or account['name']) + ': signing in again…')

        def run():
            import bulk
            return bulk.login_one((credentials['email'], credentials['password'], credentials['totp']),
                                  threading.Event(), lambda status: None)

        def done(auth):
            self.relogin_busy.discard(account['id'])
            if account not in self.store.accounts:
                return
            try:
                self.store.replace_auth(account, auth)
                self.relogin_due.pop(account['id'], None)
                self.status.set(account['name'] + ': signed in again.')
                self.refresh(account)
            except (OSError, ValueError):
                account['quota_error'] = 'Automatic re-login could not save the new session.'
                self.update_account_quota(account)
                self.status.set(account['name'] + ': automatic re-login could not save the new session.')

        def fail(_error):
            self.relogin_busy.discard(account['id'])
            if account not in self.store.accounts:
                return
            account['status'] = 'Not logged in'
            account['quota_error'] = 'Automatic re-login failed.'
            self.update_account_quota(account)
            self.status.set(account['name'] + ': automatic re-login failed.')

        self.background(run, done, fail)

    def reset_account(self, account):
        if account['id'] in self.busy:
            self.status.set('Wait for this account’s current check/reset to finish.')
            return
        count = hub.reset_count(account.get('quota', []))
        if count is None or count == 0:
            messagebox.showinfo('Reset quota', 'No reset credits available.' if count == 0 else 'Reset count unknown. Refresh this account first.', parent=self.window)
            return
        if not messagebox.askyesno('Confirm quota reset',
                f"Reset eligible Codex quota for {account['name']}?\n\nThis consumes one available reset credit. Last checked: {count} available. This cannot be undone.",
                parent=self.window, default='no'):
            return
        # Modal dialogs run Tk events: recheck after confirmation to prevent races.
        if account['id'] in self.busy or account not in self.store.accounts:
            return
        account.setdefault('reset_pending_key', str(uuid.uuid4()))
        self.store.save_accounts()  # Persist before sending; ambiguous retries reuse this key.
        self.refresh(account, reset_key=account['reset_pending_key'])

    def apply(self, account):
        if messagebox.askyesno('Switch account', f"Switch local Codex to {account['name']} and the direct OpenAI provider?\n\nClose existing Codex sessions first. Auth/config will be backed up; unrelated settings and sessions stay unchanged.", parent=self.window):
            from local_switch import switch_account
            backup = switch_account(self.store, account)
            self.update_activity()
            self.status.set(f"Switched to {account['name']}. Reopen Codex. Backup: {backup}")
            self.refresh(account)

    def auto_refresh(self):
        if self.close_requested:
            return
        self.update_activity()
        now = time.monotonic()
        for account in self.store.accounts:
            if len(self.busy) >= 2:
                break
            identity = account['id']
            if identity in self.busy or now < self.quota_due.get(identity, 0):
                continue
            self.quota_due[identity] = now + 60
            try:
                if (self.store.profile(account)/'auth.json').exists():
                    self.refresh(account)
            except (OSError, ValueError, RuntimeError):
                account['quota_error'] = 'Automatic quota check unavailable; check Codex path/login in Settings.'
                self.update_account_quota(account)
        self.window.after(1000, self.auto_refresh)

    def account_actions(self, account):
        count = hub.reset_count(account.get('quota', []))
        actions = [('open', 'Open', lambda: self.open(account)), ('switch', 'Switch', lambda: self.apply(account)),
                   ('refresh', 'Refresh', lambda: self.refresh(account)),
                   ('reset', f"Reset({count if count is not None else '?'})", lambda: self.reset_account(account)),
                   ('login', 'Log in again', lambda: self.launch(account, 'login')),
                   ('resume', 'Resume', lambda: self.open(account, 'resume')),
                   ('folder', 'Open folder', lambda: hub.open_path(self.store.validate(account))),
                   ('details', 'Details', lambda: self.details(account))]
        import pools
        for index, policy in enumerate(pools.load_config(self.store.root)['pools']):
            label = ('Remove from' if account['id'] in policy['members'] else 'Add to') + f' Pool {index+1}'
            actions.append((f'pool-{index}', label, lambda i=index: self.toggle_pool(account, i)))
        actions.append(('delete', 'Delete account', lambda: self.delete(account)))
        return actions

    def update_pool_actions(self):
        account = next((a for a in self.store.accounts if a['id'] == self.selected_account_id), None)
        if not account or not getattr(self, 'detail_actions', None):
            return
        try:
            for key, label, operation in self.account_actions(account):
                button = self.detail_actions[key]
                if button.cget('text') != label:
                    button.configure(text=label)
        except (OSError, ValueError):
            pass  # A failed lookup must not replace last-known membership labels.

    def apply_pool_members(self, index, add=(), remove=()):
        import pools
        add, remove = list(add), list(remove)
        if not add and not remove:
            return
        config = pools.load_config(self.store.root)
        dialog = self.pool_dialog
        if dialog and dialog.window.winfo_exists() and config != dialog.config:
            raise ValueError('Pool settings changed outside this window. Close and reopen Pools before changing membership; pending edits were preserved.')
        members = config['pools'][index]['members']
        for identity in add:
            if identity not in members:
                members.append(identity)
        if remove:
            drop = set(remove)
            members[:] = [identity for identity in members if identity not in drop]
        pools.save_config(self.store.root, config)
        if dialog and dialog.window.winfo_exists():
            for identity in add + remove:
                dialog.sync_membership(config, identity, index)
        self.update_pool_actions()
        self.layout_groups()
        self.update_pool_toggle()

    def toggle_pool(self, account, index):
        import pools
        members = pools.load_config(self.store.root)['pools'][index]['members']
        if account['id'] in members:
            self.apply_pool_members(index, remove=[account['id']])
        else:
            self.apply_pool_members(index, add=[account['id']])
        self.status.set(f"Pool {index+1} membership saved for {account['name']}.")

    def more(self, account):
        menu = tk.Menu(self.window, tearoff=False)
        for key, label, operation in self.account_actions(account):
            menu.add_command(label=label, command=lambda fn=operation: self.guard(fn))
        menu.tk_popup(self.window.winfo_pointerx(), self.window.winfo_pointery())

    def details(self, account):
        dialog = tk.Toplevel(self.window)
        dialog.title('Account details · ' + account['name'])
        dialog.transient(self.window)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=24)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Note').pack(anchor='w', pady=(8, 4))
        note = tk.StringVar(value=account['note'])
        ttk.Entry(body, textvariable=note, width=62).pack(fill='x')
        ttk.Label(body, text='Priority (0 is used first in pools)').pack(anchor='w', pady=(8, 4))
        priority = tk.StringVar(value=str(hub.account_priority_value(account)))
        tk.Spinbox(body, from_=0, to=4, increment=1, width=4, wrap=False, textvariable=priority, state='readonly').pack(anchor='w')
        saved = self.store.load_login(account)
        ttk.Label(body, text='Saved login for automatic re-login: yes' if saved else 'Saved login for automatic re-login: no',
                  wraplength=480).pack(anchor='w', pady=(16, 4))
        ttk.Label(body, text='Optional email / password / 2FA to enable automatic re-login. Leave password blank to keep a saved login.',
                  wraplength=480).pack(anchor='w')
        ttk.Label(body, text='Login email').pack(anchor='w', pady=(8, 4))
        email = tk.StringVar(value=(saved or {}).get('email') or account.get('email') or '')
        ttk.Entry(body, textvariable=email, width=62).pack(fill='x')
        ttk.Label(body, text='Password').pack(anchor='w', pady=(8, 4))
        password = tk.StringVar()
        ttk.Entry(body, textvariable=password, width=62, show='•').pack(fill='x')
        ttk.Label(body, text='2FA secret (optional, leave blank if this account has no 2FA)').pack(anchor='w', pady=(8, 4))
        totp = tk.StringVar()
        ttk.Entry(body, textvariable=totp, width=62, show='•').pack(fill='x')
        clear = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text='Clear saved login', variable=clear).pack(anchor='w', pady=(12, 0))
        result = {}

        def submit():
            result['ok'] = True
            result.update(note=note.get(), priority=priority.get(), email=email.get(),
                          password=password.get(), totp=totp.get(), clear=clear.get())
            dialog.destroy()

        footer = ttk.Frame(body, padding=(0, 20, 0, 0))
        footer.pack(fill='x')
        ttk.Button(footer, text='Save', command=submit).pack(side='right')
        ttk.Button(footer, text='Cancel', command=dialog.destroy).pack(side='right', padx=8)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.grab_set()
        self.window.wait_window(dialog)
        if not result:
            return
        self.store.check_name(account['name'], result['note'])
        try:
            value = int(result['priority'])
        except ValueError:
            raise ValueError('Priority must be an integer from 0 to 4. 0 is used first.') from None
        value = hub.account_priority_value({'priority': value})
        account.update(note=result['note'].strip(), priority=value)
        if result['clear']:
            self.store.clear_login(account)
        elif result['password']:
            self.store.save_login(account, result['email'].strip() or account.get('email') or account['name'],
                                  result['password'], result['totp'].strip())
        self.store.save_accounts()
        self.render()

    def create_hub_pool(self):
        import pools
        dialog = self.pool_dialog
        if dialog and dialog.window.winfo_exists():
            dialog.create_pool()
            self.select_tier(f'Pool {len(dialog.config["pools"])}')
            return
        config = pools.load_config(self.store.root)
        pool = pools.add_pool(config)
        pools.save_config(self.store.root, config)
        self.layout_groups()
        self.select_tier(f'Pool {len(config["pools"])}')
        self.status.set(f'Pool {len(config["pools"])} created on port {pool["port"]}. Start APIs (or restart) to listen on the new port.')

    def delete_hub_pool(self):
        import pools
        index = self.active_pool_index()
        if index is None:
            return
        config = pools.load_config(self.store.root)
        if len(config['pools']) <= 2:
            raise ValueError('Keep at least two pools.')
        if not messagebox.askyesno('Delete pool', f'Delete Pool {index + 1}?\n\nAccounts stay in Hub. Restart APIs to drop this pool port.', parent=self.window):
            return
        if self.pool_picker and self.pool_picker.window.winfo_exists():
            self.pool_picker.window.destroy()
            self.pool_picker = None
        dialog = self.pool_dialog
        if dialog and dialog.window.winfo_exists():
            dialog.delete_pool(index)
        else:
            pools.remove_pool(config, index)
            pools.save_config(self.store.root, config)
            self.layout_groups()
            self.update_pool_actions()
        remaining = len(pools.load_config(self.store.root)['pools'])
        target = f'Pool {min(index + 1, remaining)}'
        if self.active_tier.get() != target and target in self.groups:
            self.select_tier(target)
        self.status.set(f'Pool {index + 1} deleted. Restart APIs to drop the old port.')

    def open_pool_picker(self):
        index = self.active_pool_index()
        if index is None:
            return
        if self.pool_picker and self.pool_picker.window.winfo_exists():
            self.pool_picker.window.destroy()
        self.pool_picker = PoolPicker(self, index)

    def update_pool_toggle(self):
        import pools
        index = self.active_pool_index()
        if getattr(self, 'pool_toggle', None) and index is not None:
            self.pool_toggle.configure(text='Add/Remove', state='normal')
        if not getattr(self, 'delete_pool_button', None):
            return
        try:
            count = len(pools.load_config(self.store.root)['pools'])
        except (OSError, ValueError):
            count = 0
        self.delete_pool_button.configure(state='disabled' if index is None or count <= 2 else 'normal')

    def delete(self, account):
        import pools
        from pool_ui import server_pid
        if any(account['id'] in pool['members'] for pool in pools.load_config(self.store.root)['pools']):
            raise ValueError('Remove this account from all API pools and stop its active pool requests before deleting it.')
        if server_pid(self.store.root):
            status = json.loads(hub.read_file(self.store.root / 'pool-status.json'))
            if status.get('inflight', {}).get(account['id'], 0):
                raise ValueError('This account still has active pool requests. Wait for completion before deleting it.')
        if any(process.poll() is None for process in self.terminals.get(account['id'], [])):
            raise ValueError('Close the terminals opened for this account before deleting it.')
        if messagebox.askyesno('Delete account', f"Delete {account['name']} and its private profile permanently?\n\nClose all terminals using this profile, including those from earlier manager sessions. Shared sessions will be preserved.", parent=self.window):
            self.store.delete(account)
            self.render()
            self.status.set('Private profile deleted permanently. Shared sessions preserved.')


def main():
    parser = argparse.ArgumentParser(description='Codex CLI Hub desktop for Linux')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'codex-cli-hub')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        window = tk.Tk()
    except tk.TclError:
        print('Cannot open display. Run from your Ubuntu desktop session (DISPLAY must be available).', file=sys.stderr)
        return 1
    try:
        store = hub.Store(args.data_dir)
    except (OSError, ValueError, RuntimeError) as error:
        window.withdraw()
        messagebox.showerror('Codex CLI Hub', str(error), parent=window)
        window.destroy()
        return 1
    try:
        Application(window, store)
        window.mainloop()
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
