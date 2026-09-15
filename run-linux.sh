#!/usr/bin/env bash
set -euo pipefail
app_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python="${CODEXSWITCH_PYTHON:-$(command -v python3 || true)}"
if [ -z "$python" ]; then
    echo 'python3 is required.' >&2
    exit 1
fi
if ! "$python" -c 'import tkinter' 2>/dev/null; then
    echo 'Python Tk is required. Linux: sudo apt install python3-tk    macOS: brew install python-tk' >&2
    exit 1
fi
exec "$python" "$app_root/linux/app.py" "$@"
