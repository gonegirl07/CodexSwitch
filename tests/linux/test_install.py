import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))


class InstallTests(unittest.TestCase):
    def test_installed_launcher_runs_outside_checkout_without_account_copy(self):
        source = Path(__file__).resolve().parents[2]
        script = source / 'linux/install.py'
        self.assertTrue(script.exists(), 'User installation not implemented')
        spec = importlib.util.spec_from_file_location('hub_install', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='hub install ') as temporary:
            home = Path(temporary)
            data = home / 'private accounts'
            data.mkdir()
            (data / 'auth.json').write_text('keep-existing-data')
            paths = module.install_layout(source, home, data)
            result = subprocess.run([str(paths['launcher']), '--help'], cwd='/tmp', capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--data-dir', result.stdout)
            self.assertEqual((data / 'auth.json').read_text(), 'keep-existing-data')
            self.assertFalse(list(paths['app'].rglob('auth.json')))
            result = subprocess.run(['systemd-analyze', '--user', 'verify', str(paths['unit'])], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            module.install_layout(source, home, data)
            self.assertTrue(paths['desktop'].exists())
            self.assertEqual(paths['launcher'].name, 'codexswitch')
            self.assertTrue(paths['alias'].is_file())
            self.assertIn('Name=CodexSwitch', paths['desktop'].read_text())
            self.assertFalse(list(paths['app'].rglob('accfree2.txt')))
