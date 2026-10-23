"""Acceptance tests against an extracted native release, never a source mock."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

PACKAGE = os.environ.get('BUGGCRAFT_TEST_PACKAGE')


@unittest.skipUnless(PACKAGE, 'Set BUGGCRAFT_TEST_PACKAGE to the built executable')
class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='node-package-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        original = Path(PACKAGE).resolve()
        self.bundle = self.base / 'application with spaces'
        shutil.copytree(original.parent, self.bundle)
        self.exe = self.bundle / original.name
        self.cwd = self.base / 'unrelated-directory'
        self.cwd.mkdir()
        self.output = self.base / 'console.log'
        self.environment = os.environ.copy()
        self.environment.pop('PYTHONPATH', None)
        self.environment.pop('PYTHONHOME', None)
        self.environment['PATH'] = (str(Path(os.environ['SystemRoot']) / 'System32')
                                    if os.name == 'nt' else '/usr/bin:/bin')

    def start(self, *args):
        console = self.output.open('ab')
        self.addCleanup(console.close)
        process = subprocess.Popen([str(self.exe), '--no-browser', *args], cwd=self.cwd,
                                   env=self.environment, stdout=console, stderr=console,
                                   stdin=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        self.addCleanup(stop)
        return process

    def wait(self, process, operation):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(self.output.read_text(encoding='utf-8', errors='replace'))
            try:
                result = operation()
                if result:
                    return result
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.1)
        self.fail('Package did not become ready:\n' + self.output.read_text(encoding='utf-8', errors='replace'))

    def test_first_setup_assets_protocol_status_and_restart_without_python(self):
        with socket.socket() as a, socket.socket() as b:
            a.bind(('127.0.0.1', 0)); b.bind(('127.0.0.1', 0))
            web_port, relay_port = a.getsockname()[1], b.getsockname()[1]
        process = self.start('--web-port', str(web_port), '--setup-host', '127.0.0.1')
        def setup_details():
            text = self.output.read_text(encoding='utf-8', errors='replace')
            token = re.search(r'First setup: https://[^\s]+token=([^\s]+)', text)
            fingerprint = re.search(r'Setup certificate SHA256: ([a-f0-9]{64})', text)
            return (token.group(1), fingerprint.group(1)) if token and fingerprint else None
        token, fingerprint = self.wait(process, setup_details)
        def request(path, body=None, pin=fingerprint):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = http.client.HTTPSConnection('127.0.0.1', web_port, context=context, timeout=3)
            try:
                connection.connect()
                self.assertEqual(hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest(), pin)
                headers = {'Content-Type': 'application/json', 'X-Setup-Token': token}
                connection.request('POST' if body else 'GET', path,
                                   body=json.dumps(body) if body else None, headers=headers)
                response = connection.getresponse()
                return response.status, response.read().decode('utf-8')
            finally:
                connection.close()
        self.assertEqual(request('/')[0], 403)
        self.assertIn('setup.js', request('/?token=' + token)[1])
        self.assertIn('textContent', request('/setup.js')[1])
        code, saved = request('/setup', dict(host='127.0.0.1', port=relay_port,
            web_port=web_port, name='Portable acceptance', username='package_admin',
            password='package acceptance secret', password_confirm='package acceptance secret',
            max_rooms=8, room_capacity=8, bandwidth_mb_s=5, public_join=False))
        self.assertEqual(code, 200, saved)
        data = self.bundle / 'data'
        pem = (data / 'web-cert.pem').read_text(encoding='ascii')
        node_pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        def status():
            code, body = request('/api/status', pin=node_pin)
            return json.loads(body) if code == 200 else None
        state = self.wait(process, status)
        self.assertEqual(state['name'], 'Portable acceptance')
        self.assertIn('bandwidth', state)
        self.assertIn('portal.js', request('/', pin=node_pin)[1])
        self.assertIn('MB/s', request('/portal.js', pin=node_pin)[1])
        code, protocol = request('/developers', pin=node_pin)
        self.assertEqual(code, 200)
        self.assertIn('节点管理的权限与流程', protocol)
        code, login = request('/api/login', dict(username='package_admin',
                              password='package acceptance secret'), pin=node_pin)
        self.assertEqual(code, 200, login)
        with socket.create_connection(('127.0.0.1', relay_port), timeout=3) as ssh:
            self.assertTrue(ssh.recv(256).startswith(b'SSH-2.0-'))
        key = (data / 'relay_host_key').read_bytes()
        process.terminate()
        process.wait(timeout=15)
        self.output.write_bytes(b'')
        process = self.start()
        self.wait(process, status)
        self.assertEqual((data / 'relay_host_key').read_bytes(), key)
        self.assertNotIn('First setup:', self.output.read_text(encoding='utf-8', errors='replace'))
        self.assertFalse((self.cwd / 'data').exists())
        logs = (data / 'logs/node.log').read_text(encoding='utf-8')
        self.assertIn('frozen=True', logs)
        self.assertIn('OS=', logs)
        self.assertNotIn('package acceptance secret', logs)

    def test_fatal_startup_is_saved_with_system_information(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0)); occupied.listen()
            process = self.start('--web-port', str(occupied.getsockname()[1]), '--setup-host', '127.0.0.1')
            self.assertNotEqual(process.wait(timeout=30), 0)
        log = (self.bundle / 'data/logs/node.log').read_text(encoding='utf-8')
        self.assertIn('Node stopped unexpectedly', log)
        self.assertIn('Traceback', log)
        self.assertIn('OS=', log)
        self.assertIn('architecture=', log)


if __name__ == '__main__':
    unittest.main()
