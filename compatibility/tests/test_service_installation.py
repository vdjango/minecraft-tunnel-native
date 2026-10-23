"""Exercise actual release files and rollback without changing the host’s services."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tunnel import service


class ServiceInstallationTests(unittest.TestCase):
    def test_installer_cli_needs_no_application_dependencies(self):
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'src/main.py'), '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--install-service', result.stdout)
        self.assertNotIn('ModuleNotFoundError', result.stderr)
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'src/main.py'),
                                 '--install-service', '--web-port', '80'],
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('1024', result.stderr)

    def test_release_copies_only_runtime_and_installs_private_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source, root = base / 'source', base / 'installed'
            (source / 'src/__pycache__').mkdir(parents=True)
            (source / 'src/main.py').write_text('print("entry")')
            (source / 'src/__pycache__/main.pyc').write_bytes(b'cache')
            (source / 'config').mkdir()
            (source / 'config/private.key').write_text('DO NOT COPY')
            (source / 'requirements.txt').write_text('some-package==1.0')
            (source / 'PROTOCOL_STANDARD.md').write_text('Protocol')
            calls = []
            def execute(args):
                calls.append([str(value) for value in args])
            with mock.patch.object(service, 'command', side_effect=execute):
                release = service.prepare_release(source, root)
            self.assertEqual((release / 'src/main.py').read_text(), 'print("entry")')
            self.assertFalse((release / 'config').exists())
            self.assertFalse((release / 'src/__pycache__').exists())
            self.assertFalse((root / 'current').exists())
            self.assertEqual(calls[0], [sys.executable, '-m', 'venv', str(release / '.venv')])
            self.assertEqual(calls[1][0], str(release / '.venv/bin/python'))
            self.assertEqual(calls[1][-1], str(release / 'requirements.txt'))
            self.assertIn('import asyncssh, aiohttp, cryptography, psutil', calls[2])

    def test_dependency_failure_does_not_switch_running_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'current').mkdir()
            (root / 'current/identity').write_text('original')
            with mock.patch.object(service, 'command', side_effect=RuntimeError('pip unavailable')):
                with self.assertRaises(RuntimeError):
                    service.prepare_release(ROOT, root)
            self.assertEqual((root / 'current/identity').read_text(), 'original')

    def test_unit_uses_main_dedicated_account_and_persistent_state(self):
        unit = service.unit_file(Path('/opt/buggcraft-node'), 18444)
        self.assertIn('src/main.py --directory /var/lib/buggcraft-node --web-port 18444', unit)
        self.assertIn('User=buggcraft-node\nGroup=buggcraft-node', unit)
        self.assertIn('StateDirectoryMode=0700', unit)
        self.assertIn('ProtectSystem=strict', unit)
        self.assertIn('Restart=on-failure', unit)
        self.assertIn('WantedBy=multi-user.target', unit)
        self.assertNotIn('init_node', unit)

    def test_unmanaged_unit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            unit = root / 'existing.service'
            original = b'[Service]\nExecStart=/usr/bin/some-other-app\n'
            unit.write_bytes(original)
            with mock.patch.object(service, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, 'unmanaged service'):
                    service.activate(root, root / 'new', unit, 18443)
                command.assert_not_called()
            self.assertEqual(unit.read_bytes(), original)

    @unittest.skipIf(os.name == 'nt', 'Release activation uses Linux symbolic links')
    def test_failed_start_restores_release_unit_and_previous_running_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, new = root / 'old', root / 'new'
            old.mkdir(); new.mkdir()
            (root / 'current').symlink_to(old, target_is_directory=True)
            unit = root / 'node.service'
            previous = service.unit_file(root, 18444).encode()
            unit.write_bytes(previous)
            calls = []
            def execute(args):
                calls.append(args)
                if args == ['systemctl', 'start', service.SERVICE]:
                    raise RuntimeError('service start failed')
            with mock.patch.object(service, 'command', side_effect=execute), mock.patch.object(
                    service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as system:
                with self.assertRaisesRegex(RuntimeError, 'service start failed'):
                    service.activate(root, new, unit, 18443)
            self.assertEqual((root / 'current').resolve(), old)
            self.assertEqual(unit.read_bytes(), previous)
            self.assertIn(['systemctl', 'stop', service.SERVICE], calls)
            self.assertEqual(system.call_args_list[-1].args[0], ['systemctl', 'start', service.SERVICE])

    @unittest.skipIf(os.name == 'nt', 'Release activation uses Linux symbolic links')
    def test_successful_activation_enables_new_release_without_touching_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / 'release'
            release.mkdir()
            data = root / 'node-state.json'
            data.write_bytes(b'identity and accounts')
            unit = root / 'node.service'
            with mock.patch.object(service, 'command') as execute, mock.patch.object(
                    service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3)):
                service.activate(root, release, unit, 18444)
            self.assertEqual((root / 'current').resolve(), release)
            self.assertEqual(data.read_bytes(), b'identity and accounts')
            self.assertIn('--web-port 18444', unit.read_text())
            self.assertIn(mock.call(['systemctl', 'enable', service.SERVICE]), execute.call_args_list)


if __name__ == '__main__':
    unittest.main()
