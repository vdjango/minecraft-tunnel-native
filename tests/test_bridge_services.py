"""Native room services, exercised through real node and bridge processes."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import ipaddress
import socket
import sys
import time
import unittest
from unittest.mock import patch

from tests import test_bridge as fixture
ROOT = fixture.ROOT
LAUNCHER = Path(os.environ.get('BUGGCRAFT_LAUNCHER_SOURCE') or
                os.environ.get('BUGGCRAFT_LAUNCHER_SDK') or ROOT.parent / 'buggcraft/src/buggcraft')


class BridgeServicesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    exchange = fixture.BridgeTests.exchange
    wait_direct = fixture.BridgeTests.wait_direct

    async def call_raw(self, process, method, params=None):
        process.stdin.write((json.dumps(dict(jsonrpc='2.0', id=1, method=method,
                                             params=params or {})) + '\n').encode())
        await process.stdin.drain()
        raw = await asyncio.wait_for(process.stdout.readline(), 35)
        self.assertTrue(raw, 'Native bridge exited')
        result = json.loads(raw)
        self.assertEqual(result['id'], 1)
        return result

    async def pair(self, directory=None, allow_mod_transfer=False, transport='relay'):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port,
            transport=transport, game_directory=str(directory or ''), chat_name='房主',
            settings={'allow_mod_transfer': allow_mod_transfer}))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'],
            transport=transport, chat_name='访客'))
        return host, guest, hosted, joined

    async def wait_transfer(self, process, session, result):
        async with asyncio.timeout(20):
            while result['state'] not in ('complete', 'error'):
                await asyncio.sleep(.02)
                result = await self.call(process, 'mods.status', dict(session=session, transfer=result['transfer']))
        return result

    async def fetch_transfer(self, process, session, item, target):
        """Wait for the independent file PeerConnection before starting a transfer."""
        async with asyncio.timeout(20):
            while True:
                response = await self.call_raw(process, 'mods.fetch', dict(
                    session=session, item=item, target=str(target),
                    kind='resources' if item.get('path') else 'mods'))
                if 'result' in response:
                    return await self.wait_transfer(process, session, response['result'])
                self.assertEqual(response['error']['code'], -32021, response)
                await asyncio.sleep(.25)

    async def test_native_manifest_pages_hash_download_and_simultaneous_game_data(self):
        directory = self.logs / '房主实例'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        for i in range(130):
            (mods / (str(i) + '-' + 'long-name-' * 12 + '.jar')).write_bytes(bytes([i]) * 2048)
        payload = b'verified native transfer\0' * 100000
        (mods / '大文件.jar').write_bytes(payload)
        host, guest, hosted, joined = await self.pair(directory, allow_mod_transfer=True)
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
        expected_hash = hashlib.sha256('\n'.join(sorted(
            item['sha256'] for item in items)).encode()).hexdigest()
        self.assertEqual(published['mods_hash'], expected_hash,
                         'Native room metadata must use the launcher mod-set hash')
        item = next(item for item in items if item['name'] == '大文件.jar')
        self.assertEqual(item['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(item['sha1'], hashlib.sha1(payload).hexdigest())
        target = self.logs / '下载目录'
        target.mkdir()
        denied = await self.call_raw(guest, 'mods.fetch', dict(session=joined['session'],
            item=item, target=str(target / item['name'])))
        self.assertEqual(denied['error']['code'], -32021)
        self.assertIn('P2P', denied['error']['message'])
        await self.exchange(joined, b'continuous gameplay' * 250000)
        self.assertFalse(list(target.iterdir()))

    async def test_host_must_authorize_fallback_mod_transfer(self):
        directory = self.logs / 'permission-instance'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        payload = b'host authorized mod'
        source = mods / 'example.jar'
        source.write_bytes(payload)
        host, guest, hosted, joined = await self.direct_pair(directory, allow_mod_transfer=False)
        item = (await self.call(host, 'mods.manifest', dict(session=hosted['session'])))['items'][0]
        target = self.logs / 'permission-download.jar'
        failed = await self.fetch_transfer(guest, joined['session'], item, target)
        self.assertEqual(failed['state'], 'error')
        self.assertIn('房主尚未允许', failed['error'])
        self.assertFalse(target.exists())
        await self.call(host, 'room.configure', dict(session=hosted['session'], settings={
            'title':'授权测试', 'public':False, 'require_invite':True, 'allow_mod_transfer':True}))
        complete = await self.fetch_transfer(guest, joined['session'], item, target)
        self.assertEqual(complete['state'], 'complete', complete)
        self.assertEqual(target.read_bytes(), payload)

    async def test_hash_mismatch_and_cancellation_never_publish_partial_mod(self):
        directory = self.logs / 'instance'
        (directory / 'mods').mkdir(parents=True)
        source = directory / 'mods' / 'example.jar'
        source.write_bytes(b'a' * 200000)
        host, guest, hosted, joined = await self.direct_pair(directory, allow_mod_transfer=True)
        manifest = await self.call(host, 'mods.manifest', dict(session=hosted['session']))
        item = manifest['items'][0]
        source.write_bytes(b'b' * 200000)
        target = self.logs / 'example.jar'
        transfer = await self.call_raw(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        while 'result' not in transfer:
            self.assertEqual(transfer['error']['code'], -32021, transfer)
            await asyncio.sleep(.25)
            transfer = await self.call_raw(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        transfer = transfer['result']
        result = await self.wait_transfer(guest, joined['session'], transfer)
        self.assertEqual(result['state'], 'error')
        self.assertIn('SHA256', result['error'])
        self.assertFalse(target.exists())
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(list(self.logs.glob('*.part')))
        transfer = await self.call_raw(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        while 'result' not in transfer:
            self.assertEqual(transfer['error']['code'], -32021, transfer)
            await asyncio.sleep(.25)
            transfer = await self.call_raw(guest, 'mods.fetch', dict(session=joined['session'], item=item, target=str(target)))
        transfer = transfer['result']
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(target.exists())
        self.assertFalse(list(self.logs.glob('*.part')))

    async def direct_pair(self, directory, allow_mod_transfer=True, limit=512):
        from tests.test_peer_stun_fallback import StunResponder, SlowGate
        from tests.network_proxy import TcpOnlyRelay

        local_ip = next((ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                         if not ipaddress.ip_address(ip).is_loopback), None)
        if local_ip is None:
            self.skipTest('A non-loopback IPv4 interface is required for ICE')
        relay = await TcpOnlyRelay(self.node.port).start(local_ip)
        self.addAsyncCleanup(relay.close)
        self.profile = dict(self.profile, server_host=local_ip, server_port=relay.port)
        alternative = await TcpOnlyRelay(self.node.port).start(local_ip, StunResponder)
        alternative.allowed = [SlowGate(), SlowGate()]
        self.addAsyncCleanup(alternative.close)
        stun_port = alternative.port

        async def add_with_stun(process):
            code = 'buggnode1-' + base64.urlsafe_b64encode(
                json.dumps(self.profile).encode()).decode().rstrip('=')
            alternative_profile = dict(self.profile, server_port=stun_port)
            return (await self.call(process, 'node.add', {
                'code': code, 'alternatives': [alternative_profile]}))['node']

        host, guest = await self.process(), await self.process()
        host_node, guest_node = await add_with_stun(host), await add_with_stun(guest)
        hosted = await self.call(host, 'room.host', dict(node=host_node, game_port=self.port,
            transport='auto', game_directory=str(directory), chat_name='Host',
            settings={'allow_mod_transfer': allow_mod_transfer, 'mod_transfer_limit_kib':limit}))
        joined = await self.call(guest, 'room.join', dict(node=guest_node, invite=hosted['invite'],
            transport='auto', chat_name='Guest'))
        joined = await self.wait_direct(guest, joined)
        return host, guest, hosted, joined

    async def test_optional_resources_manifest_and_verified_direct_download(self):
        directory = self.logs / 'resource-instance'
        payloads = {}
        for folder in ('resourcepacks', 'shaderpacks'):
            target = directory / folder / 'same.zip'
            target.parent.mkdir(parents=True)
            payloads[folder + '/same.zip'] = os.urandom(100000)
            target.write_bytes(payloads[folder + '/same.zip'])
        host, guest, hosted, joined = await self.direct_pair(directory)
        game = hosted['info']['game']
        self.assertEqual(game['mods_count'], 0)
        self.assertEqual(game['resources_count'], 2)
        manifest = await self.wait_transfer(guest, joined['session'], await self.call(
            guest, 'mods.manifest', dict(session=joined['session'], kind='resources')))
        self.assertEqual(manifest['state'], 'complete', manifest)
        self.assertEqual(len(manifest['items']), 2)
        expected = hashlib.sha256('\n'.join(sorted(
            hashlib.sha256(value).hexdigest() for value in payloads.values())).encode()).hexdigest()
        self.assertEqual(game['resources_hash'], expected)
        for item in manifest['items']:
            target = self.logs / 'guest' / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            result = await self.fetch_transfer(guest, joined['session'], item, target)
            self.assertEqual(result['state'], 'complete', result)
            self.assertEqual(result['path'], 'p2p', result)
            self.assertEqual(target.read_bytes(), payloads[item['path']])
        await self.exchange(joined)

    async def test_direct_peer_mod_manifest_and_file_use_webrtc(self):
        directory = self.logs / 'p2p-mod-instance'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        payload = os.urandom(2 * 1024 * 1024)
        (mods / 'large.jar').write_bytes(payload)
        host, guest, hosted, joined = await self.direct_pair(directory)
        manifest = await self.wait_transfer(guest, joined['session'], await self.call(
            guest, 'mods.manifest', dict(session=joined['session'])))
        self.assertEqual(manifest['state'], 'complete', manifest)
        # The manifest is metadata and may use the authenticated control
        # channel; only file bytes are required to stay on the direct path.
        self.assertEqual(manifest['path'], 'relay', manifest)
        item = manifest['items'][0]
        target = self.logs / 'p2p-large.jar'
        result = None
        from tunnel.peer_signal import PeerSignalSession
        original_send = PeerSignalSession.send
        file_attempts = set()
        retries = 0
        def delayed_answer(signal, value):
            if value.get('files') and value.get('type') == 'answer':
                file_attempts.add(value.get('attempt'))
                asyncio.get_running_loop().call_later(1, original_send, signal, value)
            else:
                original_send(signal, value)
        with patch.object(PeerSignalSession, 'send', delayed_answer):
            async with asyncio.timeout(20):
                while result is None:
                    response = await self.call_raw(guest, 'mods.fetch', dict(
                        session=joined['session'], item=item, target=str(target)))
                    if 'result' in response:
                        result = await self.wait_transfer(guest, joined['session'], response['result'])
                    else:
                        self.assertEqual(response['error']['code'], -32021, response)
                        retries += 1
                        await asyncio.sleep(.25)
        self.assertGreaterEqual(retries, 3)
        self.assertEqual(len(file_attempts), 1, 'Polling must not restart the file ICE negotiation')
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['path'], 'p2p', result)
        self.assertEqual(target.read_bytes(), payload)

    async def test_twelve_parallel_mod_downloads_keep_gameplay_and_free_slots(self):
        directory = self.logs / 'parallel-instance'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        payloads = {f'mod-{i:02}.jar': os.urandom(256 * 1024) for i in range(24)}
        for name, payload in payloads.items():
            (mods / name).write_bytes(payload)
        host, guest, hosted, joined = await self.direct_pair(directory, limit=512)
        items = (await self.call(host, 'mods.manifest', dict(session=hosted['session'])))['items']
        target = self.logs / 'parallel-downloads'
        target.mkdir()
        # The first fetch also waits for the independent file connection.
        first = await self.fetch_transfer(guest, joined['session'], items[0], target / items[0]['name'])
        self.assertEqual(first['state'], 'complete', first)
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=first['transfer']))
        for batch in (items[1:13], items[12:24]):
            transfers = []
            for item in batch:
                response = await self.call(guest, 'mods.fetch', dict(
                    session=joined['session'], item=item, target=str(target / item['name'])))
                transfers.append(response)
            # Game bytes continue over the separate gameplay connection.
            await self.exchange(joined, b'parallel mod download gameplay' * 10000)
            for transfer in transfers:
                result = await self.wait_transfer(guest, joined['session'], transfer)
                self.assertEqual(result['state'], 'complete', result)
                self.assertEqual(result['path'], 'p2p', result)
                await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=result['transfer']))
        for item in items:
            self.assertEqual((target / item['name']).read_bytes(), payloads[item['name']])
        self.assertFalse(list(target.glob('*.part')))

    async def test_mod_download_limit_and_cancel_release_all_sixteen_slots(self):
        directory = self.logs / 'bounded-instance'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        (mods / 'warmup.jar').write_bytes(b'warmup')
        payload = os.urandom(512 * 1024)
        (mods / 'large.jar').write_bytes(payload)
        host, guest, hosted, joined = await self.direct_pair(directory, limit=64)
        items = (await self.call(host, 'mods.manifest', dict(session=hosted['session'])))['items']
        warmup = next(item for item in items if item['name'] == 'warmup.jar')
        item = next(item for item in items if item['name'] == 'large.jar')
        first = await self.fetch_transfer(guest, joined['session'], warmup, self.logs / 'warmup.jar')
        self.assertEqual(first['state'], 'complete', first)
        await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=first['transfer']))
        transfers = []
        for index in range(16):
            transfers.append(await self.call(guest, 'mods.fetch', dict(
                session=joined['session'], item=item, target=str(self.logs / f'bounded-{index}.jar'))))
        denied = await self.call_raw(guest, 'mods.fetch', dict(
            session=joined['session'], item=item, target=str(self.logs / 'overflow.jar')))
        self.assertEqual(denied['error']['code'], -32010, denied)
        for transfer in transfers:
            await self.call(guest, 'mods.cancel', dict(session=joined['session'], transfer=transfer['transfer']))
        self.assertFalse(list(self.logs.glob('*.part')))
        self.assertFalse(list(self.logs.glob('bounded-*.jar')))
        complete = await self.fetch_transfer(guest, joined['session'], warmup, self.logs / 'after-cancel.jar')
        self.assertEqual(complete['state'], 'complete', complete)
        self.assertEqual((self.logs / 'after-cancel.jar').read_bytes(), b'warmup')

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

    async def test_disabled_game_bridge_has_explicit_status_on_host_and_late_guest(self):
        host, guest, hosted, joined = await self.pair()
        for process, room in ((host, hosted), (guest, joined)):
            found = False
            async with asyncio.timeout(5):
                while not found:
                    events = (await self.call(process, 'session.events', dict(session=room['session'])))['events']
                    found = any('未开启' in e.get('status', e.get('bridge', '')) for e in events)
                    await asyncio.sleep(.02)
        await self.call(guest, 'chat.send', dict(session=joined['session'], text='大厅仍可聊天'))

    @unittest.skipUnless(LAUNCHER.is_dir(), 'Launcher source required')
    async def test_launcher_endpoints_validate_full_native_manifest_and_send_chat(self):
        sys.path.insert(0, str(LAUNCHER))
        from hookup.native_transport import TunnelHost, TunnelClient
        from hookup.native_routes import close
        from hookup.profiles import RelayProfile
        from hookup.modpack import fetch_manifest, manifest_hash
        from hookup.rooms import instance_metadata
        directory = self.logs / 'launcher-instance'
        mods = directory / 'mods'
        mods.mkdir(parents=True)
        profile_dir = directory / 'versions/forge'
        profile_dir.mkdir(parents=True)
        (profile_dir / 'forge.json').write_text(json.dumps({'id': 'forge', 'clientVersion': '1.19.2',
            'libraries': [{'name': 'net.minecraftforge:forge:1.19.2-43.3.5'}]}))
        for i in range(110):
            (mods / ('模组-' + str(i) + '.jar')).write_bytes(bytes([i]) * 31)
        game = instance_metadata(directory, 'forge', directory)
        host = TunnelHost(RelayProfile(**self.profile), game_port=self.port, game_directory=directory,
                          room_settings={'game': game}, transport_policy='relay')
        endpoints, tasks = [host], []
        events = []
        host.on_chat = events.append
        try:
            tasks.append(asyncio.create_task(host.run()))
            async with asyncio.timeout(15):
                while not host.connection:
                    await asyncio.sleep(.02)
            guest = TunnelClient(RelayProfile(**self.profile), host.invite, transport_policy='relay')
            endpoints.append(guest)
            guest_events = []
            guest.on_chat = guest_events.append
            tasks.append(asyncio.create_task(guest.run()))
            async with asyncio.timeout(15):
                while not guest.connection or not any(e.get('type') == 'ready' for e in guest_events):
                    await asyncio.sleep(.02)
            for endpoint in (host, guest):
                items = await fetch_manifest(endpoint)
                self.assertEqual(len(items), 110)
                self.assertEqual(manifest_hash(items), game['mods_hash'])
            await guest.send_chat('真实启动器聊天 😀')
            async with asyncio.timeout(5):
                while not any(e.get('text') == '真实启动器聊天 😀' for e in events):
                    await asyncio.sleep(.02)
        finally:
            await asyncio.gather(*(endpoint.stop() for endpoint in endpoints))
            await asyncio.gather(*tasks)
            await close()

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
                # Real game streams must pass the Minecraft handshake gate
                # before bulk payloads; arbitrary bytes are correctly rejected.
                writer.write(fixture.GAME_HANDSHAKE)
                await writer.drain()
                self.assertEqual(await asyncio.wait_for(
                    reader.readexactly(len(fixture.GAME_HANDSHAKE)), 5), fixture.GAME_HANDSHAKE)
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

    @unittest.skipUnless(LAUNCHER.is_dir(), 'Launcher source required')
    async def test_launcher_add_code_uses_cpp_without_python_ssh_modules(self):
        sys.path.insert(0, str(LAUNCHER))
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
