"""Two C++ chat participants connected to publisher-provided vanilla servers.

Set BUGGCRAFT_CHAT_SERVER_ROOT to previously verified server jars. No player
world, online account, public relay, or game download is used by this test.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import unittest
from tests import test_bridge as fixture

ROOT = fixture.ROOT
ASSETS = Path(os.environ.get('BUGGCRAFT_CHAT_SERVER_ROOT', ROOT.parent / 'multiplayer-tests' / 'chat-servers'))
PROFILES = json.loads((ROOT / 'bridge' / 'protocol_data' / 'chat_protocols.json').read_text(encoding='utf-8'))
VERSIONS = os.environ.get('BUGGCRAFT_CHAT_VERSIONS', '1.6.4,1.8.9,1.19,1.19.2,1.20.4,1.21.8,26.3').split(',')


class NativeGameChatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add

    async def exercise(self, version):
        protocol = 78 if version == '1.6.4' else next(int(key) for key, row in PROFILES.items() if version in row['releases'])
        directory = self.logs / ('vanilla-' + version)
        directory.mkdir()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        (directory / 'eula.txt').write_text('eula=true\n')
        (directory / 'server.properties').write_text(
            f'server-ip=127.0.0.1\nserver-port={port}\nonline-mode=false\n'
            'enforce-secure-profile=false\nwhite-list=false\nmax-players=10\nview-distance=3\n'
            'simulation-distance=3\nlevel-type=flat\ngamemode=1\n'
            'difficulty=peaceful\nspawn-protection=0\nnetwork-compression-threshold=256\n')
        console = (directory / 'console.log').open('wb')
        java = ROOT.parent / 'multiplayer-tests/java25/mojang/bin/java.exe'
        java = str(java) if protocol >= 775 and java.exists() else 'java'
        server = await asyncio.create_subprocess_exec(java, '-Xms256M', '-Xmx1G', '-jar',
            str((ASSETS / version / 'server.jar').resolve()), 'nogui', cwd=directory,
            stdin=asyncio.subprocess.PIPE, stdout=console, stderr=asyncio.subprocess.STDOUT)
        bridges = []
        try:
            async with asyncio.timeout(90):
                while b'Done (' not in (directory / 'console.log').read_bytes():
                    self.assertIsNone(server.returncode, (directory / 'console.log').read_text(errors='replace'))
                    self.assertFalse(list((directory / 'crash-reports').glob('*.txt')),
                        (directory / 'console.log').read_text(errors='replace'))
                    await asyncio.sleep(.1)
            for index in range(2):
                process = await self.process()
                node = await self.add(process)
                room = await self.call(process, 'room.host', dict(node=node, game_port=port,
                    transport='relay', game_chat_enabled=True, game_chat_name='NativeChat' + str(index),
                    chat_name='Lobby' + str(index), settings={'game': {'version': version, 'protocol': protocol}}))
                bridges.append((process, room['session']))
                async with asyncio.timeout(25):
                    while True:
                        events = (await self.call(process, 'session.events', {'session': room['session']}))['events']
                        if any(event.get('type') == 'bridge' and event.get('status') == '游戏聊天已同步' for event in events):
                            break
                        failures = [event for event in events if '不兼容' in event.get('status', '') or '无法识别' in event.get('status', '')]
                        self.assertFalse(failures, (version, failures, (directory / 'console.log').read_text(errors='replace')))
                        await asyncio.sleep(.05)
            detected = await self.call(bridges[0][0], 'game.status', dict(port=port, timeout_ms=4000))
            self.assertEqual(detected['protocol'], protocol)
            for turn in range(4):
                sender, receiver = bridges[turn % 2], bridges[(turn + 1) % 2]
                text = 'native-' + version + '-' + str(turn) + ' 中文 😀'
                await self.call(sender[0], 'chat.send', dict(session=sender[1], text=text))
                async with asyncio.timeout(10):
                    while True:
                        events = (await self.call(receiver[0], 'session.events', {'session': receiver[1]}))['events']
                        found = [event for event in events if event.get('source') == 'game' and text in event.get('text', '')]
                        if found:
                            self.assertEqual(len(found), 1)
                            break
                        await asyncio.sleep(.05)
                await asyncio.sleep(.65)
            await asyncio.sleep(2)
            async with asyncio.timeout(8):
                while True:
                    room = await self.call(bridges[0][0], 'room.request',
                        dict(session=bridges[0][1], request={'op': 'describe'}))
                    # Each room has its own bot, both connected to this vanilla server.
                    if room.get('game_players') == 1:
                        break
                    await asyncio.sleep(.2)
            self.assertEqual(self.node.rooms[room['id']].game_max_players, 10)
        finally:
            for process, session in bridges:
                await self.call(process, 'room.close', {'session': session})
            if server.returncode is None:
                server.stdin.write(b'stop\n')
                try:
                    await asyncio.wait_for(server.wait(), 20)
                except TimeoutError:
                    server.kill()
                    await server.wait()
            console.close()


def install_tests():
    for version in VERSIONS:
        async def check(self, version=version):
            await self.exercise(version)
        check = unittest.skipUnless(shutil.which('java') and (ASSETS / version / 'server.jar').exists(),
            'Previously verified vanilla jar and Java required')(check)
        setattr(NativeGameChatTests, 'test_vanilla_' + version.replace('.', '_'), check)


install_tests()
if __name__ == '__main__':
    unittest.main()
