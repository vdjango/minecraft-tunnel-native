"""Account-authenticated upload, consent gate, truncation and real bridge receipt."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import unittest
import sys
from unittest.mock import patch

import asyncssh
from tests import test_web_accounts as fixture


class NativeFeedbackTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.WebAccountTests.asyncSetUp
    asyncTearDown = fixture.WebAccountTests.asyncTearDown
    create_user = fixture.WebAccountTests.create_user
    login_user = fixture.WebAccountTests.login_user

    async def profile(self):
        user = await self.create_user('native-reporter')
        await self.login_user(self.clients[0], user)
        return (await (await self.clients[0].post('/api/profile', json={})).json())['profile']

    def metadata(self):
        return dict(title='IPv4 P2P test', description='User explicitly submitted this archive',
                    game_version='1.21.1', launcher_version='test', system='test', loader='vanilla')

    async def connection(self, profile):
        return await asyncssh.connect(profile['server_host'], profile['server_port'],
            username='directory:rooms', password=json.dumps({'token': profile['token']}),
            known_hosts=([asyncssh.import_public_key(profile['server_public_key'])], [], []),
            client_keys=[], agent_path=None, config=None, x509_trusted_certs=None,
            x509_trusted_cert_paths=[])

    async def test_no_consent_or_oversize_creates_no_issue(self):
        profile = await self.profile()
        async with await self.connection(profile) as connection:
            for consent, size in ((False, 1), (True, 20 * 1024 * 1024 + 1)):
                async with connection.create_process(subsystem='buggcraft-feedback-upload-v1', encoding=None) as process:
                    process.stdin.write(json.dumps(dict(consent=consent, size=size,
                        name='diagnostics.zip', metadata=self.metadata())).encode() + b'\n')
                    process.stdin.write_eof()
                    result = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                    self.assertFalse(result['ok'])
        self.assertEqual(self.portals[0].feedback.snapshot(), [])

    async def test_large_upload_with_slow_disk_preserves_other_control_requests(self):
        profile = await self.profile()
        payload = b'large-diagnostic-line\n' * 250000
        real_thread = asyncio.to_thread
        disk_released = asyncio.Event()
        reading_paused = asyncio.Event()
        async def slow_disk(function, *args, **kwargs):
            if getattr(function, '__name__', '') == 'write':
                await disk_released.wait()
            return await real_thread(function, *args, **kwargs)
        real_send = self.relays[0].send
        def send(command):
            real_send(command)
            if command.get('op') == 'channel_read_pause' and command['paused']:
                reading_paused.set()
        async with await self.connection(profile) as connection:
            async with connection.create_process(subsystem='buggcraft-feedback-upload-v1', encoding=None) as process:
                process.stdin.write(json.dumps(dict(consent=True, size=len(payload),
                    name='diagnostics.zip', metadata=self.metadata())).encode() + b'\n')
                ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                self.assertTrue(ready['ready'])
                async def upload():
                    for offset in range(0, len(payload), 65536):
                        process.stdin.write(payload[offset:offset + 65536])
                        await process.stdin.drain()
                    process.stdin.write_eof()
                    return json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
                with patch('asyncio.to_thread', side_effect=slow_disk), \
                        patch.object(self.relays[0], 'send', side_effect=send) as commands:
                    task = asyncio.create_task(upload())
                    try:
                        # Hold the actual file writer until the real native SSH
                        # channel applies backpressure, independent of CPU speed.
                        await asyncio.wait_for(reading_paused.wait(), 8)
                        self.assertFalse(task.done(), 'Large upload must still be writing to disk')
                        async with connection.create_process(subsystem='buggcraft-rooms-v1', encoding=None) as control:
                            control.stdin.write(b'{"op":"ping"}\n')
                            self.assertTrue(json.loads(await asyncio.wait_for(control.stdout.readline(), 3))['ok'])
                        disk_released.set()
                        result = await asyncio.wait_for(task, 35)
                    finally:
                        disk_released.set()
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    pauses = [call.args[0]['paused'] for call in commands.call_args_list
                              if call.args[0].get('op') == 'channel_read_pause']
                    self.assertIn(True, pauses, 'Large upload must exercise real native read backpressure')
                    self.assertIn(False, pauses, 'Native channel must resume to finish the upload')
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['size'], len(payload))
                self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
                issue = self.portals[0].feedback.issue(ready['issue'])
                attachment, = issue['attachments']
                saved = self.portals[0].feedback.root / ready['issue'] / (attachment['id'] + '.bin')
                self.assertEqual(saved.read_bytes(), payload)

    async def test_truncated_upload_keeps_issue_and_removes_partial_file(self):
        profile = await self.profile()
        async with await self.connection(profile) as connection:
            async with connection.create_process(subsystem='buggcraft-feedback-upload-v1', encoding=None) as process:
                process.stdin.write(json.dumps(dict(consent=True, size=10,
                    name='diagnostics.zip', metadata=self.metadata())).encode() + b'\n')
                ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                self.assertTrue(ready['ready'])
                process.stdin.write(b'half'); process.stdin.write_eof()
                result = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                self.assertFalse(result['ok'])
                self.assertEqual(result['issue'], ready['issue'])
        self.assertEqual(self.portals[0].feedback.issue(ready['issue'])['attachments'], [])
        self.assertFalse(list(self.portals[0].feedback.root.glob('*/*.part')))

    async def test_real_native_bridge_upload_receipt_matches_private_attachment(self):
        profile = await self.profile()
        root = Path(__file__).resolve().parents[2]
        archive = Path(self.temp.name) / 'diagnostics.zip'
        from zipfile import ZipFile
        with ZipFile(archive, 'w') as output:
            output.writestr('network.json', '{"nat_type":"test"}')
        binary = os.environ.get('BUGGCRAFT_BRIDGE_EXECUTABLE',
            str(root / 'build' / ('buggcraft-bridge.exe' if os.name == 'nt' else 'buggcraft-bridge')))
        process = await asyncio.create_subprocess_exec(binary, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def call(method, params):
            process.stdin.write(json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)).encode() + b'\n')
            await process.stdin.drain()
            value = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
            self.assertNotIn('error', value, value)
            return value['result']
        try:
            code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(profile).encode()).decode().rstrip('=')
            node = (await call('node.add', {'code': code}))['node']
            result = await call('feedback.submit', dict(node=node, consent=True,
                archive=str(archive), metadata=self.metadata()))
            self.assertTrue(result['ok'])
            self.assertEqual(result['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
            issue = self.portals[0].feedback.issue(result['issue'])
            self.assertEqual(len(issue['attachments']), 1)
            attachment = issue['attachments'][0]
            self.assertEqual((self.portals[0].feedback.root / result['issue'] /
                              (attachment['id'] + '.bin')).read_bytes(), archive.read_bytes())
            self.assertEqual((await self.clients[0].get('/api/feedback/' + result['issue'] +
                '/attachments/' + attachment['id'])).status, 401)
            login = await self.clients[0].post('/api/login', json={'username': 'admin', 'password': self.password})
            self.assertEqual(login.status, 200)
            download = await self.clients[0].get('/api/feedback/' + result['issue'] +
                                                '/attachments/' + attachment['id'])
            self.assertEqual(download.status, 200)
            self.assertEqual(await download.read(), archive.read_bytes())
            await self.portals[1].feedback.sync_once()
            login = await self.clients[1].post('/api/login', json={'username': 'admin', 'password': self.password})
            self.assertEqual(login.status, 200)
            download = await self.clients[1].get('/api/feedback/' + result['issue'] +
                                                '/attachments/' + attachment['id'])
            self.assertEqual(download.status, 200)
            self.assertEqual(await download.read(), archive.read_bytes())
        finally:
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 10)
            self.assertNotIn(profile['token'].encode(), await process.stderr.read())

    async def test_real_launcher_export_and_independent_bridge_upload(self):
        launcher = Path(os.environ.get('BUGGCRAFT_LAUNCHER_SOURCE') or
                        os.environ.get('BUGGCRAFT_LAUNCHER_SDK') or
                        Path(__file__).resolve().parents[3] / 'buggcraft/src/buggcraft')
        if not launcher.is_dir():
            self.skipTest('Launcher source checkout required for end-to-end acceptance')
        sys.path.insert(0, str(launcher))
        from hookup.profiles import RelayProfile
        from utils.feedback_upload import submit_diagnostics
        from zipfile import ZipFile
        profile = await self.profile()
        logs = Path(self.temp.name) / 'launcher-logs'
        logs.mkdir()
        # Public STUN collection is validated separately against the real network;
        # this case checks the full exporter, native adapter and private node store.
        from utils.network_diagnostics import capture_network
        def passive(directory, trace):
            return capture_network(directory, trace, active_probe=False)
        with patch.dict(os.environ, BUGGCRAFT_LOG_DIR=str(logs)), \
                patch('utils.network_diagnostics.capture_network', side_effect=passive):
            result = await submit_diagnostics(RelayProfile(**profile), 'IPv4 联机诊断',
                '测试环境中由用户明确确认后提交', consent=True)
        self.assertTrue(result['ok'])
        issue = self.portals[0].feedback.issue(result['issue'])
        attachment = issue['attachments'][0]
        path = self.portals[0].feedback.root / result['issue'] / (attachment['id'] + '.bin')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result['sha256'])
        with ZipFile(path) as archive:
            self.assertIn('p2p-evidence.json', archive.namelist())
            self.assertTrue(any(name.startswith('p2p-sessions/network-export-') for name in archive.namelist()))
        self.assertEqual(issue['title'], 'IPv4 联机诊断')
