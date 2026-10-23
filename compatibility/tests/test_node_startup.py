"""Generated configurations boot the real entry point and release listeners."""
import asyncio
import configparser
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

import asyncssh
from aiohttp import ClientSession, Fingerprint
from cryptography import x509
from cryptography.hazmat.primitives import hashes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tunnel.provisioning import initialize

spec = importlib.util.spec_from_file_location('node_entry', ROOT / 'src/main.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


class InitializationTests(unittest.TestCase):
    def test_generated_identity_profile_and_certificate_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            base = initialize(folder, 'node.example', 3334, name='Test node')
            profile = json.loads((base / 'relay-profile.json').read_text())
            key = asyncssh.read_private_key(base / 'relay_host_key')
            self.assertEqual(profile['server_public_key'], key.export_public_key().decode())
            self.assertEqual(profile['token'], (base / 'relay.token').read_text())
            cert = x509.load_pem_x509_certificate((base / 'web-cert.pem').read_bytes())
            self.assertIn('node.example', cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
            credentials = (base / 'admin-credentials.txt').read_text()
            self.assertIn(cert.fingerprint(hashes.SHA256()).hex(), credentials)
            state = json.loads((base / 'node-state.json').read_text(encoding='utf-8'))
            self.assertFalse(state['registration_open'])
            self.assertFalse(state['public_join'])
            self.assertNotIn(profile['token'], credentials)

    def test_reinitialization_preserves_every_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base = initialize(folder, 'node.example', 3334)
            before = {path.name: path.read_bytes() for path in base.iterdir()}
            with self.assertRaises(ValueError):
                initialize(folder, 'other.example', 3335)
            self.assertEqual({path.name: path.read_bytes() for path in base.iterdir()}, before)

    def test_invalid_addresses_and_ports_do_not_generate_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, (host, port, web) in enumerate([
                ('https://node.example', 3334, 18443), ('bad host', 3334, 18443),
                ('', 3334, 18443), ('node.example', 0, 18443),
                ('node.example', 3334, 65536), ('node.example', 3334, 3334),
            ]):
                target = Path(folder) / str(index)
                with self.subTest(host=host, port=port, web=web), self.assertRaises(ValueError):
                    initialize(target, host, port, web_port=web)
                self.assertEqual(list(target.iterdir()), [])

    def test_proxy_profile_keeps_origin_listener_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            base = initialize(folder, 'origin.example', 3334,
                              entry_host='proxy.example', entry_port=443)
            profile = json.loads((base / 'relay-profile.json').read_text())
            config = configparser.ConfigParser()
            config.read(base / 'server.conf', encoding='utf-8')
            self.assertEqual((profile['server_host'], profile['server_port']), ('proxy.example', 443))
            self.assertEqual(config['relay'].getint('port'), 3334)
            self.assertEqual(config['relay']['host'], '0.0.0.0')

    def test_cli_missing_configuration_exits_unsuccessfully(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(ROOT / 'src/main.py'), '--config',
                                     str(Path(folder) / 'missing.conf')],
                                    capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Relay configuration file missing', result.stderr)


class NodeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_https_and_ssh_start_and_cancel_releases_both_ports(self):
        with tempfile.TemporaryDirectory() as folder:
            ssh_port, web_port = ports()
            base = initialize(folder, '127.0.0.1', ssh_port, web_port=web_port, name='Boot test')
            path = base / 'server.conf'
            path.write_text(path.read_text().replace('0.0.0.0', '127.0.0.1'))
            cert = x509.load_pem_x509_certificate((base / 'web-cert.pem').read_bytes())
            pin = Fingerprint(cert.fingerprint(hashes.SHA256()))
            task = asyncio.create_task(entry.run(path))
            try:
                async with ClientSession() as client, asyncio.timeout(10):
                    while True:
                        if task.done():
                            await task
                            self.fail('Node exited before listening')
                        try:
                            async with client.get(f'https://127.0.0.1:{web_port}/api/status', ssl=pin) as response:
                                self.assertEqual(response.status, 200)
                                status = await response.json()
                                break
                        except OSError:
                            await asyncio.sleep(.05)
                self.assertEqual(status['name'], 'Boot test')
                profile = json.loads((base / 'relay-profile.json').read_text())
                async with asyncssh.connect('127.0.0.1', ssh_port, username='directory:rooms',
                    password=json.dumps({'token': profile['token']}),
                    known_hosts=([asyncssh.import_public_key(profile['server_public_key'])], [], []),
                    config=None, agent_path=None, client_keys=[], x509_trusted_certs=None,
                    x509_trusted_cert_paths=[]) as connection:
                    async with connection.create_process(subsystem='buggcraft-rooms-v1') as process:
                        process.stdin.write('{"op":"ping"}\n')
                        reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
                        self.assertTrue(reply['ok'])
                        self.assertIn('room-invite-policy-v1', reply['result']['capabilities'])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for port in (ssh_port, web_port):
                with self.subTest(port=port), self.assertRaises(OSError):
                    await asyncio.open_connection('127.0.0.1', port)
