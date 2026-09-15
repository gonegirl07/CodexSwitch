#!/usr/bin/env python3
"""Install the desktop launcher and a non-root systemd user API service."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import hub
import pools

UNIT = 'codex-hub-pools.service'
FILES = ['run-linux.sh', 'linux/app.py', 'linux/hub.py', 'linux/bulk.py', 'linux/bulk_ui.py',
         'linux/account_add.py', 'linux/account_add_ui.py',
         'linux/local_switch.py',
         'linux/pools.py', 'linux/pool_server.py', 'linux/pool_compat.py', 'linux/pool_ui.py',
         'linux/install.py', 'linux/requirements-pools.txt', 'linux/requirements-bulk.txt',
         'docs/LINUX.md', 'docs/POOLS.md', 'assets/logo.png', 'LICENSE']


def systemd_quote(value):
    return json.dumps(str(value).replace('%', '%%').replace('$', '$$'))


def python_bin():
    return sys.executable or '/usr/bin/python3'


def has_systemd():
    return sys.platform.startswith('linux') and shutil.which('systemctl')


def install_layout(source, user_home, data_root, python=None):
    python = python or python_bin()
    app = user_home / '.local/share/codexswitch'
    launcher = user_home / '.local/bin/codexswitch'
    alias = user_home / '.local/bin/codex-hub'
    desktop = user_home / '.local/share/applications/codexswitch.desktop'
    unit = user_home / '.config/systemd/user' / UNIT
    for relative in FILES:
        target = hub.ordinary(app / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        hub.atomic_write(target, (source / relative).read_bytes())
    for path in (launcher, alias, desktop, unit):
        hub.ordinary(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    command = f'{shlex.quote(python)} {shlex.quote(str(app / "linux/app.py"))} --data-dir {shlex.quote(str(data_root))}'
    script = ('#!/bin/sh\n# CodexSwitch launcher\nexec '+command+' "$@"\n').encode()
    hub.atomic_write(launcher, script)
    launcher.chmod(0o755)
    hub.atomic_write(alias, script)
    alias.chmod(0o755)
    # Desktop Exec escaping is not shell escaping; quote the executable directly.
    desktop_exec = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    hub.atomic_write(desktop, (f'[Desktop Entry]\nType=Application\nName=CodexSwitch\nComment=Run many Codex accounts at once, auto login, API pools\nExec="{desktop_exec}"\nIcon={app / "assets/logo.png"}\nTerminal=false\nCategories=Development;Utility;\n').encode())
    desktop.chmod(0o644)
    server = app / 'linux/pool_server.py'
    hub.atomic_write(unit, (f'# CodexSwitch managed service\n# Hub data: {data_root}\n# Hub script: {server}\n'
        '[Unit]\nDescription=CodexSwitch API pools\nAfter=network-online.target\n\n'
        '[Service]\nType=simple\n'
        f'ExecStart={systemd_quote(python)} {systemd_quote(server)} --data-dir {systemd_quote(data_root)}\n'
        'Restart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=true\nTimeoutStopSec=25\n\n'
        '[Install]\nWantedBy=default.target\n').encode())
    return {'app': app, 'launcher': launcher, 'alias': alias, 'desktop': desktop, 'unit': unit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-start', action='store_true', help='Install files only; do not enable/start service')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your desktop user, without sudo.')
    python = python_bin()
    subprocess.run([python, '-c', 'import aiohttp, tkinter, tomlkit'], check=True)
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'codex-cli-hub'
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (data / 'pools.json').exists():
        pools.save_config(data, pools.load_config(data))
    systemd = has_systemd()
    if not args.no_start and systemd:
        from pool_ui import server_pid, managed_service
        if managed_service(data):
            subprocess.run(['systemctl', '--user', 'stop', UNIT], check=True)
        pid = server_pid(data)
        if pid:
            raise SystemExit('Stop existing standalone pool APIs in Hub, then run the installer again.')
    paths = install_layout(Path(__file__).resolve().parents[1], Path.home(), data, python=python)
    if not args.no_start and systemd:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', UNIT], check=True)
    print('Installed:', paths['launcher'])
    if systemd and not args.no_start:
        print('API service:', UNIT)
        print('For boot before login, check: loginctl show-user "$USER" -p Linger')
    print('Accounts and pool settings preserved:', data)


if __name__ == '__main__':
    main()
