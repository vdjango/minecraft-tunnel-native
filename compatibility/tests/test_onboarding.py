"""Exercise real HTTPS setup requests, persisted settings and the service handoff."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tunnel.onboarding import SetupServer, setup_tls
from tunnel.portal import verify_password
from tunnel.provisioning import initialize

spec = importlib.util.spec_from_file_location('onboarding_entry', ROOT / 'src/main.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def free_ports():
    with socket.socket() as a, socket.socket() as b:
        a.bind(('127.0.0.1', 0))
        b.bind(('127.0.0.1', 0))
        return a.getsockname()[1], b.getsockname()[1]


def fields(port, web_port):
    return dict(host='127.0.0.1', port=port, web_port=web_port, name='Wizard node',
                username='node_admin', password='correct horse battery staple',
                password_confirm='correct horse battery staple', public_join=True,
                max_rooms=7, room_capacity=11, bandwidth_mb_s=2)


class SetupHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'node'
        self.server = SetupServer(self.directory)
        tls, _ = setup_tls(self.temp.name)
        self.runner = web.AppRunner(self.server.app, access_log=None)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0, ssl_context=tls)
        await self.site.start()
        self.url = 'https://127.0.0.1:' + str(self.site._server.sockets[0].getsockname()[1])
        self.client = ClientSession()
        self.addAsyncCleanup(self.client.close)
        self.data = fields(free_ports()[0], 18443)

    async def post(self, data=None, **kwargs):
        return await self.client.post(self.url + '/setup', json=self.data if data is None else data,
            headers=kwargs.pop('headers', {'X-Setup-Token': self.server.token}), ssl=False, **kwargs)

    async def test_private_page_and_unauthorized_posts_never_create_state(self):
        for token in ['', 'wrong', '中文']:
            response = await self.client.get(self.url + '/?token=' + token, ssl=False)
            self.assertEqual(response.status, 403)
            self.assertNotIn(self.server.token, await response.text())
            response = await self.post(headers={'X-Setup-Token': token})
            self.assertEqual(response.status, 403)
        self.assertFalse(self.directory.exists())
        response = await self.client.get(self.url + '/?token=' + self.server.token, ssl=False)
        self.assertEqual(response.status, 200)
        self.assertIn('管理员账号', await response.text())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    async def test_rejects_cross_origin_and_invalid_configuration_without_keys(self):
        response = await self.post(headers={'X-Setup-Token': self.server.token, 'Origin': 'https://attacker.test'})
        self.assertEqual(response.status, 403)
        invalid = [dict(password_confirm='different'), dict(host='https://invalid.test'),
                   dict(password='short', password_confirm='short'), dict(username='管理员'),
                   dict(max_rooms=129), dict(room_capacity=0), dict(bandwidth_mb_s='nan'),
                   dict(port=18443), dict(port=80), dict(web_port=443),
                   dict(name=''), dict(public_join='yes')]
        for change in invalid:
            with self.subTest(change=list(change)):
                response = await self.post(dict(self.data, **change))
                self.assertEqual(response.status, 400, await response.text())
                self.assertFalse((self.directory / 'server.conf').exists())
                self.assertEqual(list(self.directory.iterdir()) if self.directory.exists() else [], [])
        self.assertFalse(self.server.finished.is_set())

    async def test_success_saves_real_password_hash_policy_and_closes_setup(self):
        response = await self.post()
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())['url'], 'https://127.0.0.1:18443/admin')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['Connection'], 'close')
        state = json.loads((self.directory / 'node-state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['admin_user'], 'node_admin')
        self.assertTrue(verify_password(self.data['password'], state['admin_hash']))
        self.assertEqual(state['capacity']['max_rooms'], 7)
        self.assertEqual(state['capacity']['room_capacity'], 11)
        self.assertEqual(state['capacity']['bandwidth_mb_s'], 2)
        self.assertTrue(state['public_join'])
        self.assertFalse(state['registration_open'])
        for path in self.directory.iterdir():
            self.assertNotIn(self.data['password'].encode(), path.read_bytes(), path.name)
        before = {p.name:p.read_bytes() for p in self.directory.iterdir()}
        self.assertEqual((await self.post()).status, 410)
        self.assertEqual({p.name:p.read_bytes() for p in self.directory.iterdir()}, before)
        self.assertTrue(self.server.finished.is_set())

    async def test_simultaneous_initialization_cannot_replace_admin_or_keys(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.server.initialize
        async def hold_save(*args):
            entered.set()
            await release.wait()
            await original(*args)
        with mock.patch.object(self.server, 'initialize', side_effect=hold_save):
            first = asyncio.create_task(self.post())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                second = await self.post(dict(self.data, username='other_admin'))
                self.assertEqual(second.status, 409, await second.text())
            finally:
                release.set()
                response = await first
        self.assertEqual(response.status, 200, await response.text())
        state = json.loads((self.directory / 'node-state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['admin_user'], self.data['username'])
        self.assertTrue(verify_password(self.data['password'], state['admin_hash']))
        self.assertEqual((await self.post(dict(self.data, username='other_admin'))).status, 410)

    async def test_occupied_tcp_or_udp_port_is_reported_before_saving(self):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with self.subTest(kind=kind), socket.socket(socket.AF_INET, kind) as listener:
                listener.bind(('0.0.0.0', 0))
                response = await self.post(dict(self.data, port=listener.getsockname()[1]))
                self.assertEqual(response.status, 409, await response.text())
                self.assertFalse(self.directory.exists())
        self.assertEqual((await self.post()).status, 200)

    async def test_disk_failure_leaves_no_partial_configuration_and_allows_retry(self):
        original = __import__('os').fdopen
        writes = 0
        def fail_once(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 3:
                __import__('os').close(args[0])
                raise OSError('simulated disk failure')
            return original(*args, **kwargs)
        with mock.patch('tunnel.provisioning.os.fdopen', side_effect=fail_once), self.assertLogs('tunnel.onboarding', 'ERROR'):
            response = await self.post()
        self.assertEqual(response.status, 500)
        self.assertNotIn('simulated', await response.text())
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertFalse(self.server.finished.is_set())
        self.assertEqual((await self.post()).status, 200)


class FirstRunHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_entry_switches_https_to_running_node_and_reuses_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            relay_port, web_port = free_ports()
            directory = Path(folder) / 'data'
            server = SetupServer(directory, web_port=web_port)
            args = argparse.Namespace(config=None, directory=str(directory), web_port=web_port, setup_host='127.0.0.1')
            async with ClientSession() as client:
                with mock.patch('tunnel.onboarding.SetupServer', return_value=server), mock.patch('builtins.print'):
                    task = asyncio.create_task(entry.serve(args))
                    try:
                        async with asyncio.timeout(15):
                            while True:
                                if task.done():
                                    await task
                                    self.fail('Service stopped before initialization')
                                try:
                                    response = await client.get(f'https://127.0.0.1:{web_port}/?token={server.token}', ssl=False)
                                    await response.read()
                                    if response.status == 200:
                                        break
                                except OSError:
                                    pass
                                await asyncio.sleep(.05)
                        response = await client.post(f'https://127.0.0.1:{web_port}/setup',
                            json=fields(relay_port, web_port), headers={'X-Setup-Token':server.token}, ssl=False)
                        self.assertEqual(response.status, 200, await response.text())
                        await response.read()
                        async with asyncio.timeout(15):
                            while True:
                                if task.done():
                                    await task
                                try:
                                    response = await client.get(f'https://127.0.0.1:{web_port}/api/status', ssl=False)
                                    if response.status == 200:
                                        status = await response.json()
                                        break
                                    await response.read()
                                except OSError:
                                    pass
                                await asyncio.sleep(.05)
                        self.assertEqual(status['name'], 'Wizard node')
                        self.assertEqual(status['max_rooms'], 7)
                        response = await client.post(f'https://127.0.0.1:{web_port}/setup', json={}, ssl=False)
                        self.assertEqual(response.status, 404)
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                before = (directory / 'relay_host_key').read_bytes()
                # On restart the wizard is never constructed, even with a different setup port.
                args.web_port = 1
                with mock.patch('tunnel.onboarding.first_run', side_effect=AssertionError('wizard reopened')):
                    task = asyncio.create_task(entry.serve(args))
                    try:
                        async with asyncio.timeout(10):
                            while True:
                                if task.done():
                                    await task
                                try:
                                    response = await client.get(f'https://127.0.0.1:{web_port}/api/status', ssl=False)
                                    if response.status == 200:
                                        self.assertEqual((await response.json())['name'], 'Wizard node')
                                        break
                                except OSError:
                                    pass
                                await asyncio.sleep(.05)
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                self.assertEqual((directory / 'relay_host_key').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
