#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python3 -m unittest discover -s tests/linux -v
mkdir -p dist
# Explicit allowlist: never include runtime profiles, settings or credentials.
tar -czf dist/codex-cli-hub-linux.tar.gz --transform='s,^,codex-cli-hub-linux/,' \
    install.sh package.json run-linux.sh linux/app.py linux/hub.py linux/bulk.py linux/bulk_ui.py linux/requirements-bulk.txt \
    linux/account_add.py linux/account_add_ui.py \
    linux/local_switch.py \
    linux/pools.py linux/pool_server.py linux/pool_compat.py linux/pool_ui.py linux/install.py linux/requirements-pools.txt \
    docs/LINUX.md docs/POOLS.md LICENSE assets/logo.png
echo "Package: $repo_root/dist/codex-cli-hub-linux.tar.gz"
