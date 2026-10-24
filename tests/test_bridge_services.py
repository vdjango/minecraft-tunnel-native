"""Native room services, exercised through real node and bridge processes."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

from tests import test_bridge as fixture
ROOT = fixture.ROOT


class BridgeServicesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    exchange = fixture.BridgeTests.exchange

    async def pair(self, directory=None):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port,
            transport='relay', game_directory=str(directory or ''), chat_name='房主'))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'],
            transport='relay', chat_name='访客'))
        return host, guest, hosted, joined

    async def wait_transfer(self, process, session, result):
        async with asyncio.timeout(20):
            while result['state'] not in ('complete', 'error'):
                await asyncio.sleep(.02)
                result = await self.call(process, 'mods.status', dict(session=session, transfer=result['transfer']))
        return result

    async def test_native_manifest_pages_hash_download_and_simultaneous_game_data(self):
        directory = self.logs / '房主实例'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        for i in range(130):
            (mods / (str(i) + '-' + 'long-name-' * 12 + '.jar')).write_bytes(bytes([i]) * 2048)
        payload = b'verified native transfer\0' * 100000
        (mods / '大文件.jar').write_bytes(payload)
        host, guest, hosted, joined = await self.pair(directory)
        published = hosted['info']['game']
        self.assertEqual(published['mods_count'], 131)
        self.assertEqual(len(published['mods_hash']), 64)
        self.assertLessEqual(len(published['mods']), 24)
        result = await self.wait_transfer(guest, joined['session'], await self.call(
            guest, 'mods.manifest', dict(session=joined['session'])))
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['total'], 131)
        items = list(result['items'])
        while len(items) < result['total']:
            page = await self.call(guest, 'mods.status', dict(session=joined['session'],
                transfer=result['transfer'], offset=len(items)))
            self.assertEqual(page['offset'], len(items))
            items += page['items']
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=result['transfer']))
        item = next(item for item in items if item['name'] == '大文件.jar')
        self.assertEqual(item['sha256'], hashlib.sha256(payload).hexdigest())
        target = self.logs / '下载目录'
        target.mkdir()
        transfer = await self.call(guest, 'mods.fetch', dict(session=joined['session'],
            item=item, target=str(target / item['name'])))
        result, _ = await asyncio.gather(self.wait_transfer(guest, joined['session'], transfer),
            self.exchange(joined, b'continuous gameplay' * 250000))
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual((target / item['name']).read_bytes(), payload)
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(list(target.glob('*.part')))

    async def test_hash_mismatch_and_cancellation_never_publish_partial_mod(self):
        directory = self.logs / 'instance'
        (directory / 'mods').mkdir(parents=True)
        source = directory / 'mods' / 'example.jar'
        source.write_bytes(b'a' * 200000)
        host, guest, hosted, joined = await self.pair(directory)
        manifest = await self.call(host, 'mods.manifest', dict(session=hosted['session']))
        item = manifest['items'][0]
        source.write_bytes(b'b' * 200000)
        target = self.logs / 'example.jar'
        transfer = await self.call(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        result = await self.wait_transfer(guest, joined['session'], transfer)
        self.assertEqual(result['state'], 'error')
        self.assertIn('SHA256', result['error'])
        self.assertFalse(target.exists())
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(list(self.logs.glob('*.part')))
        transfer = await self.call(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(target.exists())
        self.assertFalse(list(self.logs.glob('*.part')))

    async def test_chat_waits_for_ack_and_uses_authenticated_native_hello(self):
        host, guest, hosted, joined = await self.pair()
        async with asyncio.timeout(5):
            while True:
                events = await self.call(guest, 'session.events', dict(session=joined['session']))
                if any(event.get('type') == 'ready' for event in events['events']):
                    break
                await asyncio.sleep(.02)
        self.assertEqual(await self.call(guest, 'chat.send', dict(session=joined['session'], text='原生聊天 😀')), {'sent': True})
        found = []
        async with asyncio.timeout(5):
            while not found:
                events = await self.call(host, 'session.events', dict(session=hosted['session']))
                found = [event for event in events['events'] if event.get('text') == '原生聊天 😀']
                await asyncio.sleep(.02)
        self.assertEqual(found[0]['sender'], '访客')
        self.assertEqual(found[0]['source'], 'lobby')
        await self.exchange(joined)

    async def test_personal_code_and_native_social_signature(self):
        from tunnel.federation import Federation
        from tunnel.accounts import Accounts
        from tests.account_fixture import enroll
        import secrets
        federation = Federation(self.key, self.profile, network_key=secrets.token_urlsafe(48), name='test')
        federation.relay, self.node.federation = self.node, federation
        # Match the production callback for account-backed node access codes.
        self.node.account_authenticate = lambda value: federation.accounts.authenticate(value)
        federation.accounts = Accounts(federation, self.logs / 'accounts.json')
        federation.publish()
        self.addAsyncCleanup(federation.close)
        account, credential = enroll(federation.accounts, 'native_alice', delegated=True)
        target, _ = enroll(federation.accounts, 'native_bob')
        profile = dict(self.profile, token=credential)
        process = await self.process()
        code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(profile).encode()).decode().rstrip('=')
        node = (await self.call(process, 'node.add', {'code': code}))['node']
        await self.call(process, 'social.operate', {'node': node, 'operation': 'request', 'target': target})
        result = await self.call(process, 'node.request', {'node': node, 'request': {'op': 'social'}})
        self.assertTrue(result)
        events = federation.accounts.social.snapshot()
        self.assertTrue(any(event['grant']['a'] == account and event['action']['target'] == target
                            for event in events), events)

    async def test_control_budget_preserves_requests_and_continuous_game_data(self):
        host, guest, hosted, joined = await self.pair()
        done = asyncio.Event()
        samples = []
        async def controls():
            try:
                for _ in range(36):
                    info = await self.call(host, 'room.request',
                        dict(session=hosted['session'], request={'op': 'describe'}))
                    self.assertEqual(info['id'], hosted['room'])
            finally:
                done.set()
        async def gameplay():
            port = int(joined['address'].rsplit(':', 1)[1])
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            payload = b'control-budget-game-data' * 2000
            try:
                while not done.is_set():
                    started = time.perf_counter()
                    writer.write(payload)
                    await writer.drain()
                    self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 5), payload)
                    samples.append((time.perf_counter() - started) * 1000)
                    await asyncio.sleep(.02)
            finally:
                writer.close()
                await writer.wait_closed()
        await asyncio.wait_for(asyncio.gather(controls(), gameplay()), 30)
        self.assertGreater(len(samples), 20)
        self.assertLess(max(samples), 1000, 'Control budget waiting blocked game forwarding')

    @unittest.skipUnless((ROOT.parent / 'buggcraft' / 'src').is_dir(), 'Adjacent launcher checkout required')
    async def test_launcher_add_code_uses_cpp_without_python_ssh_modules(self):
        sys.path.insert(0, str(ROOT.parent / 'buggcraft' / 'src' / 'buggcraft'))
        try:
            from hookup.nodes import decode_code, probe, encode_code
            from hookup.native_routes import manager, close
            code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(self.profile).encode()).decode().rstrip('=')
            profile = await probe(decode_code(code))
            self.assertEqual(encode_code(profile).split('-')[0], 'buggnode1')
            info = await manager().request('bridge.initialize', {})
            self.assertEqual(info['build']['implementation'], 'cpp')
            self.assertIn('social.operate', info['methods'])
        finally:
            await close()


if __name__ == '__main__':
    unittest.main()
