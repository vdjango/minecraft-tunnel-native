"""Real service acceptance for a disposable Linux host; explicitly enabled in CI."""
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('BUGGCRAFT_TEST_SYSTEMD') == '1'


@unittest.skipUnless(ENABLED, 'Requires a disposable systemd host and BUGGCRAFT_TEST_SYSTEMD=1')
class SystemdServiceTests(unittest.TestCase):
    def test_install_browser_setup_restart_and_upgrade_preserve_identity(self):
        self.assertEqual(sys.platform, 'linux')
        self.assertEqual(os.geteuid(), 0)
        self.assertTrue(Path('/run/systemd/system').is_dir())
        unit = Path('/etc/systemd/system/buggcraft-node.service')
        data = Path('/var/lib/buggcraft-node')
        self.assertFalse(unit.exists(), 'This acceptance test requires an unused disposable host')
        self.assertFalse(data.exists(), 'Refusing to use existing node data')
        self.addCleanup(subprocess.run, ['systemctl', 'disable', '--now', 'buggcraft-node'],
                        check=False, capture_output=True)
        def execute(args):
            result = subprocess.run(args, capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            return result.stdout
        def request(path, body=None, token=None):
            payload = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request('https://127.0.0.1:18443' + path, data=payload,
                headers={'Content-Type':'application/json', **({'X-Setup-Token': token} if token else {})})
            return urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=3)
        def wait_for(path):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with request(path) as response:
                        return json.load(response)
                except (OSError, ValueError):
                    time.sleep(.1)
            self.fail('Service HTTPS endpoint did not become ready')
        execute([sys.executable, str(ROOT / 'src/main.py'), '--install-service'])
        token = None
        for _ in range(100):
            logs = execute(['journalctl', '-u', 'buggcraft-node', '-n', '30', '--no-pager', '-o', 'cat'])
            found = re.findall(r'First setup: https://SERVER_IP:18443/\?token=([A-Za-z0-9_-]+)', logs)
            if found:
                token = found[-1]
                break
            time.sleep(.1)
        self.assertIsNotNone(token, 'Service did not print the private setup URL')
        with request('/?token=' + token) as response:
            self.assertIn('setup-form', response.read().decode())
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            relay_port = probe.getsockname()[1]
        fields = dict(name='Native service test', host='127.0.0.1', port=relay_port, web_port=18443,
            username='test_admin', password='integration secret password',
            password_confirm='integration secret password', public_join=False,
            max_rooms=7, room_capacity=11, bandwidth_mb_s=2)
        with request('/setup', fields, token) as response:
            self.assertTrue(json.load(response)['complete'])
        self.assertEqual(wait_for('/api/status')['name'], 'Native service test')
        self.assertEqual(data.stat().st_mode & 0o777, 0o700)
        self.assertNotEqual(data.stat().st_uid, 0)
        before = {name:(data / name).read_bytes() for name in
                  ('relay_host_key', 'relay.token', 'node-state.json')}
        execute(['systemctl', 'is-enabled', '--quiet', 'buggcraft-node'])
        execute(['systemctl', 'restart', 'buggcraft-node'])
        self.assertEqual(wait_for('/api/status')['max_rooms'], 7)
        current = Path('/opt/buggcraft-node/current').resolve()
        execute([sys.executable, str(ROOT / 'src/main.py'), '--install-service'])
        self.assertNotEqual(Path('/opt/buggcraft-node/current').resolve(), current)
        self.assertEqual(wait_for('/api/status')['name'], 'Native service test')
        self.assertEqual({name:(data / name).read_bytes() for name in before}, before)
        with self.assertRaises(urllib.error.HTTPError) as error:
            request('/setup', fields, token)
        self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
