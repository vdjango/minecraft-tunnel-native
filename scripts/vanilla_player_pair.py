"""Two unmodified Mojang GUI clients: A owns a room, B joins via native C++.

Runs only in an isolated fresh world, using the workspace's existing Ubuntu SSH
trust. Credentials/invites stay in memory. Only test-owned processes are stopped.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import time
import uuid

import vanilla_multiplayer as vanilla

ROOT, WORKSPACE, JAVA = vanilla.ROOT, vanilla.WORKSPACE, vanilla.JAVA
NAMES = ['PairHostA', 'PairGuestB']


class NativeBridge:
    def __init__(self, profile, directory):
        self.profile, self.directory = profile, directory
        self.sessions = []

    async def start(self):
        self.directory.mkdir(parents=True)
        self.stderr = (self.directory / 'stderr.log').open('wb')
        binary = Path(os.environ.get('BUGGCRAFT_BRIDGE_EXECUTABLE', ROOT / 'build/buggcraft-bridge.exe'))
        self.process = await asyncio.create_subprocess_exec(
            str(binary), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=self.stderr,
            env=dict(os.environ, BUGGCRAFT_BRIDGE_LOG_DIR=str(self.directory / 'players')),
            creationflags=0x08000000)
        encoded = base64.urlsafe_b64encode(json.dumps(self.profile).encode()).decode().rstrip('=')
        self.node = (await self.call('node.add', dict(code='buggnode1-' + encoded)))['node']
        return self

    async def call(self, method, params):
        self.process.stdin.write((json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)) + '\n').encode())
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), 40)
        result = json.loads(line)
        if result.get('error'):
            raise RuntimeError(result['error'].get('message', 'Native bridge request failed'))
        value = result['result']
        if method in ('room.host', 'room.join'):
            self.sessions.append(value['session'])
        return value

    async def close(self):
        try:
            for session in reversed(self.sessions):
                if self.process.returncode is None:
                    await self.call('room.close', dict(session=session))
        finally:
            if self.process.returncode is None:
                self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(), 15)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.stderr.close()


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


async def read_packet(reader):
    size = 0
    for shift in range(0, 35, 7):
        byte = (await reader.readexactly(1))[0]
        size |= (byte & 127) << shift
        if not byte & 128:
            if size > 1048576:
                raise ValueError('Oversized status response')
            return await reader.readexactly(size)
    raise ValueError('Invalid status length')


async def status_ping(port):
    start = time.perf_counter()
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    try:
        handshake = b'\0' + varint(772) + b'\x09localhost' + struct.pack('!H', port) + b'\x01'
        writer.write(varint(len(handshake)) + handshake + b'\x01\x00')
        await writer.drain()
        status = await read_packet(reader)
        if status[0] != 0:
            raise ValueError('Unexpected Minecraft status response')
        connected = (time.perf_counter() - start) * 1000
        packet = b'\x01' + struct.pack('!Q', time.perf_counter_ns())
        start = time.perf_counter()
        writer.write(b'\x09' + packet)
        await writer.drain()
        if await read_packet(reader) != packet:
            raise ValueError('Minecraft status ping mismatch')
        return dict(connect_and_status_ms=round(connected, 3), ping_rtt_ms=round((time.perf_counter() - start) * 1000, 3))
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    artifact = ROOT / 'validation-artifacts' / time.strftime('vanilla-pair-%Y%m%d-%H%M%S')
    artifact.mkdir(parents=True)
    print('PAIR_GAME_ARTIFACT ' + str(artifact), flush=True)
    report = dict(passed=False, version='1.21.8', node='Ubuntu native C++', bridge='Windows native C++',
                  arrangement='A connects to own vanilla server; B joins A room through native bridge',
                  clients=[], status_samples=[], death_respawn=[])
    binary = Path(os.environ.get('BUGGCRAFT_BRIDGE_EXECUTABLE', ROOT / 'build/buggcraft-bridge.exe'))
    report['bridge_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    processes, bridges, outputs = [], [], []
    ssh = remote_in = remote_out = tunnel = server = observer = watcher = None
    remote_logs = None
    try:
        ssh = await asyncio.to_thread(vanilla.connect)
        with ssh.open_sftp() as sftp:
            sftp.put(str(ROOT / 'scripts/benchmark_server.py'), '/tmp/buggcraft-native-validation/scripts/benchmark_server.py')
        remote_logs = '/tmp/buggcraft-native-validation/' + artifact.name + '-players'
        remote_in, remote_out, _ = await asyncio.to_thread(ssh.exec_command,
            'cd /tmp/buggcraft-native-validation && .venv/bin/python scripts/benchmark_server.py native '
            '--workers 4 --bind 0.0.0.0 --diagnostics-dir ' + remote_logs)
        data = json.loads(await asyncio.wait_for(asyncio.to_thread(remote_out.readline), 25))
        public_host = ssh.get_transport().getpeername()[0]
        def reachable():
            try:
                with socket.create_connection((public_host, data['port']), timeout=4):
                    return True
            except OSError:
                return False
        if await asyncio.to_thread(reachable):
            profile = dict(server_host=public_host, server_port=data['port'], token=data['token'], server_public_key=data['key'])
            report['network_path'] = 'Direct public TCP to isolated Ubuntu native node; no administrative SSH forwarding'
        else:
            tunnel = vanilla.forward(ssh.get_transport(), data['port'])
            profile = dict(server_host='127.0.0.1', server_port=tunnel.server_address[1], token=data['token'], server_public_key=data['key'])
            report['network_path'] = 'Ubuntu native node over administrative SSH forwarding; includes forwarding overhead'
        print(report['network_path'], flush=True)
        world = artifact / 'world'
        world.mkdir()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            game_port = reservation.getsockname()[1]
        offline_uuid = lambda name: str(uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + name).encode()).digest(), version=3))
        (world / 'eula.txt').write_text('eula=true\n')
        (world / 'server.properties').write_text(
            f'server-ip=127.0.0.1\nserver-port={game_port}\nonline-mode=false\nenforce-secure-profile=false\n'
            'gamemode=creative\nforce-gamemode=true\ndifficulty=peaceful\nlevel-type=minecraft:flat\n'
            'generator-settings={"biome":"minecraft:plains","layers":[{"block":"minecraft:bedrock","height":1},'
            '{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}]}\n'
            'spawn-protection=0\nview-distance=4\nsimulation-distance=4\nnetwork-compression-threshold=256\n'
            'allow-flight=true\n', encoding='utf-8')
        (world / 'ops.json').write_text(json.dumps([dict(uuid=offline_uuid(name), name=name, level=4,
            bypassesPlayerLimit=True) for name in NAMES]), encoding='utf-8')
        server = await asyncio.create_subprocess_exec(str(JAVA), '-Xms256M', '-Xmx1G', '-jar',
            str(WORKSPACE / 'multiplayer-tests/game-server/server.jar'), 'nogui', cwd=world,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000)
        lines, ready = [], asyncio.Event()
        async def watch():
            with (artifact / 'server.log').open('w', encoding='utf-8') as log:
                while line := await server.stdout.readline():
                    text = line.decode(errors='replace')
                    lines.append(text)
                    log.write(text)
                    log.flush()
                    if 'Done (' in text:
                        ready.set()
        watcher = asyncio.create_task(watch())
        await asyncio.wait_for(ready.wait(), 100)
        server.stdin.write(b'gamerule doDaylightCycle false\ntime set day\ngamerule doMobSpawning false\n')
        await server.stdin.drain()
        for name in NAMES:
            bridge = NativeBridge(profile, artifact / (name + '-bridge'))
            bridges.append(bridge)
            await bridge.start()
        before = time.perf_counter()
        hosted = await bridges[0].call('room.host', dict(node=bridges[0].node, game_port=game_port, transport='relay'))
        report['a_create_room_ms'] = round((time.perf_counter() - before) * 1000, 3)
        before = time.perf_counter()
        joined = await bridges[1].call('room.join', dict(node=bridges[1].node, invite=hosted['invite'], transport='relay'))
        report['b_join_room_ms'] = round((time.perf_counter() - before) * 1000, 3)
        report['transport'] = joined['transport']
        guest_port = int(joined['address'].rsplit(':', 1)[1])
        ports = [game_port, guest_port]
        minecraft = next(WORKSPACE.glob('PCL */.minecraft'))
        for name, port in zip(NAMES, ports):
            game = artifact / name
            game.mkdir()
            (game / 'options.txt').write_text('lang:en_us\nrenderDistance:4\nsimulationDistance:4\nmaxFps:20\n'
                'guiScale:2\nautoJump:false\npauseOnLostFocus:false\nsoundCategory_master:0.0\nonboardAccessibility:false\n')
            options = dict(username=name, uuid=offline_uuid(name), token='0', executablePath=str(JAVA),
                jvmArguments=['-Xms256M', '-Xmx1G'], gameDirectory=str(game), launcherName='NativePairValidation',
                launcherVersion='1', customResolution=True, resolutionWidth='800', resolutionHeight='450',
                quickPlayMultiplayer=f'127.0.0.1:{port}')
            command = vanilla.minecraft_launcher_lib.command.get_minecraft_command('1.21.8', str(minecraft), options)
            output = (game / 'launch.log').open('w', encoding='utf-8')
            outputs.append(output)
            launched = time.perf_counter()
            process = await asyncio.create_subprocess_exec(*command, cwd=game, stdout=output, stderr=output, creationflags=0x08000000)
            processes.append(process)
            record = dict(name=name, pid=process.pid, game_directory=str(game))
            report['clients'].append(record)
            deadline = time.monotonic() + 120
            while not any(name + ' joined the game' in line for line in lines):
                if time.monotonic() > deadline or process.returncode is not None:
                    raise RuntimeError(name + ' did not join the vanilla game')
                await asyncio.sleep(.5)
            record['launch_to_server_join_ms'] = round((time.perf_counter() - launched) * 1000, 3)
            print('Vanilla joined: ' + name, flush=True)
        observer_log = (artifact / 'observer.log').open('w', encoding='utf-8')
        outputs.append(observer_log)
        observer = await asyncio.create_subprocess_exec('node', str(ROOT / 'scripts/vanilla_observer.cjs'),
            str(game_port), str(artifact / 'operations.json'), json.dumps(NAMES), '100',
            stdout=observer_log, stderr=observer_log,
            env=dict(os.environ, NODE_PATH=str(WORKSPACE / 'multiplayer-tests/node_modules')), creationflags=0x08000000)
        sampling = True
        async def sample():
            while sampling:
                for name, port in zip(NAMES, ports):
                    try:
                        async with asyncio.timeout(10):
                            value = await status_ping(port)
                        report['status_samples'].append(dict(player=name, at=time.time(), **value))
                    except Exception as error:
                        raise RuntimeError('In-game status probe failed for ' + name) from error
                await asyncio.sleep(.5)
        sampler = asyncio.create_task(sample())
        await asyncio.sleep(12)
        await asyncio.to_thread(vanilla.operate_windows, report['clients'], False)
        async def server_command(command, predicate):
            start = len(lines)
            server.stdin.write((command + '\n').encode())
            await server.stdin.drain()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                matches = [line for line in lines[start:] if predicate(line)]
                if matches:
                    return matches[0]
                await asyncio.sleep(.05)
            raise TimeoutError('No server confirmation for ' + command)
        for _ in range(3):
            record = report['clients'][1]
            await server_command('kill ' + record['name'], lambda line: record['name'] in line and 'Killed' in line)
            before = time.perf_counter()
            for attempt in range(6):
                await asyncio.to_thread(vanilla.respawn_window, record)
                response = await server_command('data get entity ' + record['name'] + ' Health',
                    lambda line: record['name'] in line and 'following entity data' in line)
                match = re.search(r'following entity data:\s*(-?\d+(?:\.\d+)?)f?', response)
                if match and float(match.group(1)) > 0:
                    report['death_respawn'].append(dict(attempts=attempt + 1, health=float(match.group(1)),
                        ui_to_server_seconds=round(time.perf_counter() - before, 3)))
                    break
            else:
                raise AssertionError('Guest failed to respawn')
            await asyncio.sleep(2)
        await asyncio.wait_for(observer.wait(), 130)
        sampling = False
        await sampler
        operations = json.loads((artifact / 'operations.json').read_text(encoding='utf-8'))
        if not operations.get('passed') or observer.returncode:
            raise AssertionError('Vanilla movement/block operations failed')
        report['operations'] = operations
        for record in report['clients']:
            content = (Path(record['game_directory']) / 'logs/latest.log').read_text(encoding='utf-8', errors='replace')
            if not all('[' + other + ': Changed the block at' in content for other in NAMES if other != record['name']):
                raise AssertionError('Other player block command did not reach ' + record['name'])
        if any(process.returncode is not None for process in processes):
            raise AssertionError('A real client exited during the game')
        if any('lost connection' in line and any(name in line for name in NAMES) for line in lines):
            raise AssertionError('Unexpected player disconnection')
        report['passed'] = True
    except Exception as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if 'sampler' in locals() and not sampler.done():
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        for process in ([observer] if observer else []) + processes:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for bridge in reversed(bridges):
            if hasattr(bridge, 'process'):
                await bridge.close()
        if server and server.returncode is None:
            server.stdin.write(b'stop\n')
            await server.stdin.drain()
            await asyncio.wait_for(server.wait(), 30)
            await watcher
        if remote_in:
            remote_in.write('stop\n')
            remote_in.flush()
            await asyncio.to_thread(remote_in.channel.recv_exit_status)
        if ssh and remote_logs:
            await asyncio.to_thread(vanilla.fetch_tree, ssh.get_transport(), remote_logs, artifact / 'node-players')
        if tunnel:
            await asyncio.to_thread(tunnel.shutdown)
            tunnel.server_close()
        if ssh:
            ssh.close()
        for output in outputs:
            output.close()
        summaries = []
        for path in artifact.glob('*-bridge/players/*/*.jsonl'):
            summaries += [row for line in path.read_text(encoding='utf-8').splitlines()
                          if (row := json.loads(line)).get('event') == 'compression_summary']
        raw = sum(row['raw_bytes'] for row in summaries)
        wire = sum(row['wire_bytes'] for row in summaries)
        report['bridge_compression'] = dict(raw_bytes=raw, framed_bytes=wire,
            reduction_percent=round(100 * (1 - wire / raw), 2) if raw else 0,
            scope='Native relay payload only; excludes SSH/TCP/IP headers')
        report['completed'] = time.strftime('%Y-%m-%d %H:%M:%S')
        (artifact / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('PAIR_GAME_RESULT ' + json.dumps(dict(passed=report['passed'], failure=report.get('failure'), artifacts=str(artifact))), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
