"""Pool membership/settings and process lifecycle for the desktop Hub."""
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

import hub
import pools


def managed_service(root):
    unit = Path.home() / '.config/systemd/user/codex-hub-pools.service'
    try:
        lines = unit.read_text().splitlines()
        if '# Hub data: '+str(root) in lines and (
                '# Codex CLI Hub managed service' in lines or '# CodexSwitch managed service' in lines):
            return unit.name
    except OSError:
        pass
    return None


def server_pid(root):
    path = hub.ordinary(root / 'pool-server.lock')
    if not path.exists():
        return None
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
        except BlockingIOError:
            pass
    try:
        status = json.loads(hub.read_file(root / 'pool-status.json'))
        pid = int(status['pid'])
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        script = str(Path(__file__).with_name('pool_server.py')).encode()
        scripts = [script]
        if managed_service(root):
            home = Path.home()
            scripts.append(str(home / '.local/share/codex-cli-hub-app/linux/pool_server.py').encode())
            scripts.append(str(home / '.local/share/codexswitch/linux/pool_server.py').encode())
        if any(script in command for script in scripts) and str(root).encode() in command:
            return pid
    except (OSError, ValueError, KeyError):
        pass
    return None


class PoolDialog:
    def __init__(self, app):
        self.app, self.root = app, app.store.root
        self.config = pools.load_config(self.root)
        if not (self.root / 'pools.json').exists():
            pools.save_config(self.root, self.config)
        self.process = None
        self.window = tk.Toplevel(app.window)
        self.window.title('API Pools')
        self.window.geometry('1050x850')
        self.window.transient(app.window)
        self.tabs = ttk.Notebook(self.window)
        self.tabs.pack(fill='both', expand=True, padx=18, pady=14)
        self.views = []
        for index, policy in enumerate(self.config['pools']):
            self.build_view(index, policy)
        footer = ttk.Frame(self.window, padding=(18, 0, 18, 8))
        footer.pack(fill='x')
        for label, callback in [('Save pools', self.save), ('Create pool', self.create_pool), ('Delete pool', self.delete_selected_pool), ('Reload accounts/networks', self.reload_choices), ('Start APIs', self.start), ('Stop APIs', self.stop)]:
            ttk.Button(footer, text=label, command=lambda fn=callback: app.guard(fn)).pack(side='left', padx=(0, 8))
        self.status = tk.StringVar(value='API server runs independently; closing Hub keeps active pool APIs running.')
        ttk.Label(self.window, textvariable=self.status, padding=(18, 6), wraplength=940).pack(fill='x')
        self.reload_choices()
        self.window.after(500, self.poll)

    def build_view(self, index, policy):
        body = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(body, text=f'Pool {index+1}')
        url = tk.StringVar(value=f"http://127.0.0.1:{policy['port']}/v1")
        ttk.Label(body, textvariable=url, style='Name.TLabel').pack(anchor='w')
        actions = ttk.Frame(body, padding=(0, 10))
        actions.pack(fill='x')
        for label, callback in [('Switch', lambda i=index: self.switch_local(i)), ('Copy local URL', lambda i=index: self.copy_url(i)), ('Copy LAN URL', lambda i=index: self.copy_lan(i)), ('Copy API key', lambda i=index: self.copy_key(i)), ('Client configs…', lambda i=index: self.examples(i))]:
            ttk.Button(actions, text=label, command=lambda fn=callback: self.app.guard(fn)).pack(side='left', padx=(0, 8))
        fields = ttk.Frame(body)
        fields.pack(fill='x')
        variables = {}
        for row, (label, field) in enumerate([('API port', 'port'), ('Switch at 5h remaining % ≤', 'min_5h'), ('Switch at weekly remaining % ≤', 'min_weekly'), ('Switch at 30d remaining % ≤', 'min_30d'), ('Other/unknown cycle remaining % ≤', 'min_other'), ('Native response model for image bridge', 'image_model')]):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky='w', pady=4)
            value = tk.StringVar(value=str(policy.get(field, 5 if field in ('min_30d', 'min_other') else '')))
            ttk.Entry(fields, textvariable=value, width=32).grid(row=row, column=1, sticky='ew', padx=12, pady=4)
            variables[field] = value
        ttk.Label(fields, text='Share API on network').grid(row=6, column=0, sticky='w')
        network = ttk.Combobox(fields, state='readonly', width=48)
        network.grid(row=6, column=1, sticky='ew', padx=12, pady=6)
        ttk.Label(body, text='Any reported window at its threshold triggers rotation. Absent windows are ignored.\n0 reserves nothing; exhausted or invalid quota is still blocked.', wraplength=890, padding=(0, 12)).pack(anchor='w')
        lists = ttk.Frame(body)
        lists.pack(fill='both', expand=True, pady=8)
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(2, weight=1)
        lists.rowconfigure(2, weight=1)
        ttk.Label(lists, text='Available accounts (not in this pool)').grid(row=0, column=0, sticky='w')
        ttk.Label(lists, text='Accounts in this pool').grid(row=0, column=2, sticky='w')
        available = tk.Listbox(lists, selectmode='extended', exportselection=False, height=7)
        members = tk.Listbox(lists, selectmode='extended', exportselection=False, height=7)
        available.grid(row=2, column=0, sticky='nsew')
        members.grid(row=2, column=2, sticky='nsew')
        moves = ttk.Frame(lists, padding=8)
        moves.grid(row=2, column=1)
        ttk.Button(moves, text='Add →', command=lambda i=index: self.move_members(i, True)).pack(pady=8)
        ttk.Button(moves, text='← Remove', command=lambda i=index: self.move_members(i, False)).pack(pady=8)
        state = tk.StringVar(value='Save membership, then start the API server.')
        ttk.Label(body, textvariable=state, wraplength=890).pack(anchor='w', pady=6)
        view = {'variables': variables, 'url': url, 'members': members, 'available': available,
                'member_ids': policy['members'][:], 'network': network, 'network_ids': [], 'state': state, 'filters': {}}
        self.views.append(view)
        for side, column in [('available', 0), ('members', 2)]:
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
            ttk.Button(controls, text='Select all', command=lambda i=index, s=side: self.select_visible(i, s)).pack(anchor='w', pady=(4, 0))
            view['filters'][side] = {'query': query, 'tier': tier, 'count': count}
            query.trace_add('write', lambda *args, v=view: self.refresh_members(v))
            tier.trace_add('write', lambda *args, v=view: self.refresh_members(v))
        return view

    def create_pool(self):
        self.save()
        pool = pools.add_pool(self.config)
        pools.save_config(self.root, self.config)
        self.build_view(len(self.config['pools']) - 1, pool)
        self.reload_choices()
        self.app.update_pool_actions()
        self.app.layout_groups()
        self.tabs.select(len(self.views) - 1)
        self.status.set(f'Pool {len(self.config["pools"])} created on port {pool["port"]}. Start APIs (or restart) to listen on the new port.')

    def select_visible(self, index, side):
        widget = self.views[index]['available' if side == 'available' else 'members']
        widget.selection_set(0, 'end')

    def delete_selected_pool(self):
        index = self.tabs.index(self.tabs.select())
        if not messagebox.askyesno('Delete pool', f'Delete Pool {index + 1}?\n\nAccounts stay in Hub. Restart APIs to drop this pool port.', parent=self.window):
            return
        self.delete_pool(index)

    def delete_pool(self, index):
        if len(self.config['pools']) <= 2:
            raise ValueError('Keep at least two pools.')
        self.save()
        pools.remove_pool(self.config, index)
        pools.save_config(self.root, self.config)
        for child in list(self.tabs.winfo_children()):
            self.tabs.forget(child)
            child.destroy()
        self.views.clear()
        for i, policy in enumerate(self.config['pools']):
            self.build_view(i, policy)
        self.reload_choices()
        self.app.update_pool_actions()
        self.app.layout_groups()
        remaining = len(self.config['pools'])
        self.tabs.select(min(index, remaining - 1))
        self.status.set(f'Pool {index + 1} deleted. Restart APIs to drop the old port.')

    def refresh_members(self, view):
        accounts = {a['id']: a for a in self.app.store.accounts}
        for side in ('available', 'members'):
            widget = view[side]
            id_key = 'available_ids' if side == 'available' else 'visible_member_ids'
            old = view.get(id_key, [])
            selected = {old[i] for i in widget.curselection() if i < len(old)}
            scroll = widget.yview()[0]
            identities = [i for i in accounts if i not in view['member_ids']] if side == 'available' else view['member_ids']
            filters = view['filters'][side]
            query, tier = filters['query'].get().strip().casefold(), filters['tier'].get()
            visible = []
            for identity in identities:
                account = accounts.get(identity, {})
                name = account.get('email') or account.get('name', 'Removed account · '+identity[:8])
                plan = account.get('subscription', 'Unknown')
                matches = tier == 'All' or plan == tier or (tier == 'Other' and plan not in ('Free', 'Plus'))
                if matches and query in name.casefold():
                    visible.append((identity, f'{name}  [{plan}]'))
            view[id_key] = [identity for identity, name in visible]
            widget.delete(0, 'end')
            for index, (identity, name) in enumerate(visible):
                widget.insert('end', name)
                if identity in selected:
                    widget.selection_set(index)
            widget.yview_moveto(scroll)
            filters['count'].set(f'{len(visible)}/{len(identities)} accounts')

    def move_members(self, index, add):
        view = self.views[index]
        widget, identities = (view['available'], view['available_ids']) if add else (view['members'], view['visible_member_ids'])
        selected = [identities[i] for i in widget.curselection()]
        if add:
            view['member_ids'].extend(selected)
        else:
            view['member_ids'] = [i for i in view['member_ids'] if i not in selected]
        self.refresh_members(view)
        self.status.set('Membership changed locally. Click Save pools to apply. Other pool is unchanged.')

    def sync_membership(self, config, identity, index):
        """Rebase pending membership edits; the explicit quick action wins for its ID."""
        for i, (base, latest, view) in enumerate(zip(self.config['pools'], config['pools'], self.views)):
            removed = set(base['members']) - set(view['member_ids'])
            added = [member for member in view['member_ids'] if member not in base['members']]
            if i == index:
                removed.discard(identity)
                added = [member for member in added if member != identity]
            view['member_ids'] = [member for member in latest['members'] if member not in removed]
            view['member_ids'].extend(member for member in added if member not in view['member_ids'])
            self.refresh_members(view)
        self.config = copy.deepcopy(config)

    def reload_choices(self):
        networks = pools.network_interfaces()
        for policy, view in zip(self.config['pools'], self.views):
            current = view['network'].current()
            selected = view['network_ids'][current] if current >= 0 else policy.get('interface', '')
            ids = [''] + [n['interface'] for n in networks]
            labels = ['Local only · 127.0.0.1'] + [n['label'] for n in networks]
            if selected not in ids:
                ids.append(selected)
                labels.append(selected+' · disconnected (local URL remains available)')
            view['network_ids'] = ids
            view['network']['values'] = labels
            view['network'].current(ids.index(selected))
            self.refresh_members(view)

    def save(self):
        if pools.load_config(self.root) != self.config:
            raise ValueError('Pool settings changed outside this window (for example, Add account). Close and reopen Pools before saving; newer memberships were preserved.')
        updated = copy.deepcopy(self.config)
        for policy, view in zip(updated['pools'], self.views):
            values = view['variables']
            try:
                policy.update(port=int(values['port'].get()), min_5h=float(values['min_5h'].get()), min_weekly=float(values['min_weekly'].get()), image_model=values['image_model'].get().strip(),
                              min_30d=float(values['min_30d'].get()), min_other=float(values['min_other'].get()),
                              members=view['member_ids'][:], interface=view['network_ids'][view['network'].current()])
            except ValueError:
                raise ValueError('Ports and quota thresholds must be numbers.') from None
        pools.save_config(self.root, updated)
        self.config = updated
        self.app.update_pool_actions()
        for view, policy in zip(self.views, updated['pools']):
            view['url'].set(f"http://127.0.0.1:{policy['port']}/v1")
        self.status.set('Saved. Network/port changes apply within a few seconds; membership applies to the next request. LAN uses HTTP: trusted networks only.')

    def clipboard(self, text):
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def copy_url(self, index):
        self.clipboard(self.views[index]['url'].get())
        self.status.set('Pool URL copied.')

    def copy_lan(self, index):
        policy = self.config['pools'][index]
        hosts = pools.bind_hosts(policy, pools.network_interfaces())
        if len(hosts) < 2:
            raise ValueError('Select a connected Ethernet or Wi-Fi network, then Save pools first.')
        self.clipboard(f'http://{hosts[1]}:{policy["port"]}/v1')
        self.status.set('LAN URL copied. Use this pool API key; HTTP is for trusted LAN only.')

    def switch_local(self, index):
        if not messagebox.askyesno('Switch local Codex', f'Switch local Codex to Pool {index+1}?\n\nSave pending pool settings first. Close existing Codex sessions. Config/auth will be backed up; the selected model is preserved. New Codex sessions will use this pool.', parent=self.window):
            return
        policy = pools.load_config(self.root)['pools'][index]
        if not policy['members']:
            raise ValueError('Add accounts to this pool and Save pools first.')
        if not server_pid(self.root):
            raise ValueError('Start APIs before switching local Codex to a pool.')
        from local_switch import switch_pool
        backup = switch_pool(self.app.store, policy)
        self.status.set(f'Local Codex switched to Pool {index+1}. Open a new Codex session. Backup: {backup}')

    def copy_key(self, index):
        self.clipboard(self.config['pools'][index]['key'])
        self.status.set('Pool API key copied. Keep this key private.')

    def examples(self, index):
        dialog = tk.Toplevel(self.window)
        dialog.title('Client configuration · ' + self.config['pools'][index]['id'])
        dialog.geometry('850x650')
        text = tk.Text(dialog, wrap='none')
        text.pack(fill='both', expand=True)
        text.insert('1.0', pools.client_examples(self.config['pools'][index]))
        text.configure(state='disabled')
        ttk.Button(dialog, text='Copy config examples', command=lambda: self.clipboard(text.get('1.0', 'end-1c'))).pack(pady=8)

    def start(self):
        self.save()
        if server_pid(self.root):
            self.status.set('Pool APIs already running.')
            return
        if managed_service(self.root):
            subprocess.run(['systemctl', '--user', 'start', managed_service(self.root)], check=True, timeout=10)
            self.status.set('Starting installed API service…')
            return
        if not importlib.util.find_spec('aiohttp'):
            raise ValueError('Install pool dependencies: python3 -m pip install --user -r linux/requirements-pools.txt')
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('pool_server.py')), '--data-dir', str(self.root)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.status.set('Starting API listeners…')

    def stop(self):
        if managed_service(self.root):
            subprocess.run(['systemctl', '--user', 'stop', managed_service(self.root)], check=True, timeout=30)
            self.status.set('Installed API service stopped.')
            return
        pid = server_pid(self.root)
        if pid:
            os.kill(pid, signal.SIGTERM)
            self.status.set('Stopping pool APIs; in-flight requests get up to 10 seconds to finish.')
        else:
            self.status.set('Pool APIs are stopped.')

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            pid = server_pid(self.root)
            if pid:
                status = json.loads(hub.read_file(self.root / 'pool-status.json'))
                self.status.set('APIs running · '+str(pid)+' · '+(' | '.join(status.get('network_errors', [])) or 'Closing Hub keeps APIs running.'))
                for index, view in enumerate(self.views):
                    identity = status.get('current', {}).get(str(index))
                    account = next((a for a in self.app.store.accounts if a['id'] == identity), None)
                    quota = status.get('usage', {}).get(identity) or {}
                    windows = quota.get('windows') or {}
                    details = ' · '.join(f"{name}: {window['remaining']:g}%" for name, window in windows.items())
                    view['state'].set('Active: ' + (account['name'] if account else 'waiting for a request') + ('\n' + details if details else '') + ('\n' + status.get('errors', {}).get(identity, '') if identity else ''))
                    urls = [f'http://{entry["host"]}:{entry["port"]}/v1' for entry in status.get('listeners', []) if entry['pool'] == index]
                    if urls:
                        view['state'].set(view['state'].get()+'\nListening: '+' · '.join(urls))
            elif self.process and self.process.poll() is not None and self.process.returncode:
                self.status.set('Server did not start. Check whether a port is in use; run linux/pool_server.py for diagnosis.')
        except (OSError, ValueError, KeyError):
            pass
        self.window.after(1500, self.poll)
