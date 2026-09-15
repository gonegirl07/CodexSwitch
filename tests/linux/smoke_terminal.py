"""Desktop integration: launch the real terminal with a synthetic CLI, never a real login."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import hub

with tempfile.TemporaryDirectory(prefix='hub-terminal-') as temporary:
    base = Path(temporary)
    fixture = Path(__file__).with_name('fake_codex.py').resolve()
    process = hub.launch(str(fixture), base, base, 'login')
    deadline = time.monotonic() + 15
    output = base / 'terminal-check.json'
    while not output.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            break
        time.sleep(0.1)
    assert output.exists(), f'Terminal helper did not run (exit {process.poll()}).'
    assert json.loads(output.read_text()) == {'cwd': str(base), 'home': str(base)}
    process.wait(timeout=5)
    assert process.returncode == 0
    status = hub.refresh(shutil.which('codex'), base)
    assert status['status'] == 'Not logged in', status['status']
print('Desktop terminal launch passed; installed Codex confirmed the synthetic profile is not logged in.')
