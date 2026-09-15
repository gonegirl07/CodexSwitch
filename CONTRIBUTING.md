# Contributing

Author: [Gonegirl07](https://github.com/gonegirl07).

The hub is the Python 3.10+ / Tk app in `linux/`. Codex CLI is required. Do not send credentials, `auth.json`, `login.json`, `accounts.json`, `pools.json`, or a used data directory.

```bash
python3 -m unittest discover -s tests/linux -v
```

GUI smoke (needs a display or xvfb):

```bash
xvfb-run -a python3 tests/linux/smoke_gui.py
```

Keep changes scoped to the Linux hub. Tests use synthetic data and do not log into real accounts.
