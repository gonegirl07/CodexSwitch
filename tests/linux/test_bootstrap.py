import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.TestCase):
    def test_package_exposes_one_command_bin(self):
        package = json.loads((ROOT / 'package.json').read_text())
        self.assertEqual(package['name'], 'codexswitch')
        self.assertEqual(package['bin']['codexswitch'], './install.sh')
        installer = ROOT / 'install.sh'
        self.assertTrue(installer.is_file())
        text = installer.read_text()
        self.assertTrue(text.startswith('#!/usr/bin/env bash'))
        self.assertIn('npx github:gonegirl07/CodexSwitch', text)
        self.assertIn('bunx github:gonegirl07/CodexSwitch', text)
        syntax = subprocess.run(['bash', '-n', str(installer)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_secret_account_files_are_ignored(self):
        ignore = (ROOT / '.gitignore').read_text()
        for name in ('accfree2.txt', 'accgptfree.md', 'Capture.PNG',
                     'windows-codex-ssh-handoff.md', '.zcode/', '**/login.json'):
            self.assertIn(name, ignore)
