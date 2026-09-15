#!/usr/bin/env bash
# CodexSwitch one-command installer for Linux, Armbian, and macOS.
# Usage: npx github:gonegirl07/CodexSwitch
#        bunx github:gonegirl07/CodexSwitch
#        ./install.sh
set -euo pipefail

REPO_URL="${CODEXSWITCH_REPO:-https://github.com/gonegirl07/CodexSwitch.git}"

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

os="$(uname -s)"
case "$os" in
    Linux|Darwin) ;;
    *) die "CodexSwitch installer supports Linux, Armbian, and macOS." ;;
esac

here="$0"
while [ -L "$here" ]; do
    dir="$(cd -P "$(dirname "$here")" && pwd)"
    here="$(readlink "$here")"
    case "$here" in
        /*) ;;
        *) here="$dir/$here" ;;
    esac
done
root="$(cd -P "$(dirname "$here")" 2>/dev/null && pwd || true)"

if [ ! -f "${root:-}/linux/app.py" ]; then
    command -v git >/dev/null || die "git is required to download CodexSwitch."
    work="${TMPDIR:-/tmp}/codexswitch-src-$$"
    rm -rf "$work"
    git clone --depth 1 "$REPO_URL" "$work"
    root="$work"
fi

python="$(command -v python3 || true)"
[ -n "$python" ] || die "python3 is required."

install_tk() {
    if [ "$os" = Darwin ]; then
        command -v brew >/dev/null || die "Python Tk is missing. Install it with: brew install python-tk"
        brew install python-tk
        python="$(command -v python3 || true)"
        return
    fi
    if command -v apt-get >/dev/null; then
        sudo apt-get update -y
        sudo apt-get install -y python3 python3-tk python3-pip
        return
    fi
    die "Python Tk is missing. Install python3-tk (apt) or python-tk (brew)."
}

if ! "$python" -c 'import tkinter' 2>/dev/null; then
    install_tk
    python="$(command -v python3 || true)"
    "$python" -c 'import tkinter' 2>/dev/null || die "Python Tk is still missing after install."
fi

if ! "$python" -c 'import aiohttp, tomlkit' 2>/dev/null; then
    if "$python" -m pip install --user -r "$root/linux/requirements-pools.txt"; then
        :
    elif "$python" -m pip install --user --break-system-packages -r "$root/linux/requirements-pools.txt"; then
        :
    else
        die "Could not install Python packages. Try: $python -m pip install --user -r linux/requirements-pools.txt"
    fi
fi

if ! printf '%s' ":$PATH:" | grep -q ":${HOME}/.local/bin:"; then
    printf 'Add ~/.local/bin to PATH, then run: codexswitch\n'
fi
export PATH="${HOME}/.local/bin:${PATH}"
"$python" "$root/linux/install.py" --no-start

launcher="${HOME}/.local/bin/codexswitch"
[ -x "$launcher" ] || die "Install finished but $launcher was not created."

printf 'Installed %s\n' "$launcher"

if [ "$os" = Linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    printf 'No desktop display. From a graphical session run: codexswitch\n'
    exit 0
fi

exec "$launcher" "$@"
