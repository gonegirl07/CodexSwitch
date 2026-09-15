# Architecture

CodexSwitch (Gonegirl07) is the Python 3.10+ / Tk Hub in `linux/`. It is not an OpenAI product.

| Module | Role |
| --- | --- |
| `linux/app.py` | Desktop: account cards, tabs, quota, Open / Switch / Reset |
| `linux/hub.py` | Isolated `CODEX_HOME` per account, auth file store, quota via Codex app-server, terminal launch |
| `linux/account_add.py` | Browser OAuth (PKCE, `127.0.0.1:1455`) and `auth.json` import |
| `linux/bulk.py` | Parallel browser login `email\|password\|2FA` |
| `linux/pools.py` | Pool membership, ports, keys, quota thresholds, priority 0–4 |
| `linux/pool_server.py` | Local/LAN API that rotates accounts by remaining quota |
| `linux/pool_compat.py` | Chat Completions and Images adapters |
| `linux/local_switch.py` | Point local Codex at one account or one pool |
| `linux/install.py` | User-level launcher `codexswitch` (systemd pool unit on Linux) |

Data: `${XDG_DATA_HOME:-$HOME/.local/share}/codex-cli-hub`. Details: [LINUX.md](LINUX.md), [POOLS.md](POOLS.md).
