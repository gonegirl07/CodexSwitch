# CodexSwitch

Author: [Gonegirl07](https://github.com/gonegirl07)

Linux desktop hub (Python 3.10+ / Tk) for Linux, Armbian, and macOS. Manage many Codex CLI profiles at once: isolated homes, many project terminals, auto-login, live quota, and API pools that rotate accounts on the 5-hour / weekly windows.

Not an OpenAI product. Credentials stay on your machine.

## Install

```bash
npx github:gonegirl07/CodexSwitch
```

```bash
bunx github:gonegirl07/CodexSwitch
```

```bash
git clone https://github.com/gonegirl07/CodexSwitch.git
cd CodexSwitch
./install.sh
```

Then run `codexswitch`. Linux/Armbian need `python3-tk` (the installer uses `apt` if it is missing). macOS: `brew install python-tk`. Codex CLI must be on PATH. Start it from a desktop session (a display is required).

[Full Linux guide](docs/LINUX.md) · [API Pools](docs/POOLS.md) · [Changelog](CHANGELOG.md) · [MIT](LICENSE)

## What it does

From the Linux app in `linux/` and [docs/LINUX.md](docs/LINUX.md):

- **Many accounts, many projects.** Card names are always the account email. **Open** picks a project from session metadata or Browse, then starts Codex in its own terminal. Closing the hub does not kill terminals. **Resume** runs `codex resume --all` in the chosen project.
- **Auto login.** **+ Add** supports Browser OAuth (copy the link or open a browser; callback `127.0.0.1:1455`), import `auth.json`, copy the existing Codex login, or **Bulk** (1–6 browsers, `email|password|2FA`). A profile is saved only after login succeeds. Optional local email / password / 2FA re-login when the session dies or returns 401.
- **Live quota.** Hub reads quota on launch, after add/switch, and about every 30 seconds. The green bar follows API window length (`5h`, `Weekly`, `30d`, …) and does not invent a 5-hour window for weekly-only accounts. GPT Reserve is separate from Codex quota. **Reset(n)** spends one reset after confirmation.
- **Tabs and filters.** All / Pool 1 / Pool 2 / … plus plan tabs (Free, Plus, Pro, Business, …) when those accounts exist. Search by email, Remaining / Exhausted filter. Priority 0–4 on pool tabs (0 is used first).
- **Switch.** On an account: point local Codex at that account’s `auth.json` and the direct OpenAI provider. On a pool tab: point local Codex at that pool. Auth/config are backed up; unrelated model and history settings stay.
- **API pools.** Two pools by default, more if you add them. Each pool has its own URL and API key (`http://127.0.0.1:8311/v1`, `8312`, …). Requests rotate across members by remaining quota for Codex, pi, OpenClaw, and image tools. ChatGPT login tokens are not used as client keys. Details: [docs/POOLS.md](docs/POOLS.md).

## Data

Default `${XDG_DATA_HOME:-$HOME/.local/share}/codex-cli-hub` (mode `700`). Profile files `600`. Only `profiles/<id>/sessions` is a symlink to the original Codex sessions directory. Do not commit this folder.

```bash
./run-linux.sh --data-dir /absolute/path/to/hub-data
```

## Tests

```bash
python3 -m unittest discover -s tests/linux -v
```
