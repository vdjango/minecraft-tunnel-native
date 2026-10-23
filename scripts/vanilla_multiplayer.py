"""Four real vanilla GUI clients through the isolated Ubuntu native node.

Uses the workspace's installed, unmodified Mojang jars and existing SSH trust.
All worlds, client directories, logs and screenshots belong to this run only.
"""
import asyncio
import ctypes
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import select
import re
import socketserver
import stat
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path[:0] = [str(ROOT / 'scripts'), str(WORKSPACE / 'buggcraft/src/buggcraft')]
import minecraft_launcher_lib
from hookup.relay import RelayProfile, TunnelHost, TunnelClient
from remote_validate import connect

JAVA = Path(r'C:\Program Files\Microsoft\jdk-21.0.8.9-hotspot\bin\java.exe')
NAMES = ['GuiHost', 'GuiGuest1', 'GuiGuest2', 'GuiGuest3']
ARTIFACTS = ROOT / 'validation-artifacts' / time.strftime('vanilla-%Y%m%d-%H%M%S')


def load_gui():
    spec = importlib.util.spec_from_file_location('vanilla_gui', WORKSPACE / 'multiplayer-tests/gui_clients.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = ARTIFACTS
    return module


def operate_windows(records, capture=True):
    """Deliver Win32 input messages to each actual GLFW client window.

    Remote/automated desktop sessions can forbid foreground activation. Posting
    to the owned client windows also avoids sending keystrokes to the IDE.
    """
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.SetProcessDPIAware()
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    windows = {}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in {record['pid'] for record in records} and user.IsWindowVisible(handle):
            windows[pid.value] = handle
        return True
    user.EnumWindows(callback_type(visit), 0)
    width, height = user.GetSystemMetrics(0), user.GetSystemMetrics(1)
    def key(handle, code):
        scan = user.MapVirtualKeyW(code, 0)
        user.PostMessageW(handle, 0x100, code, 1 | (scan << 16))
        time.sleep(.05)
        user.PostMessageW(handle, 0x101, code, 1 | (scan << 16) | (3 << 30))
    def command(handle, text):
        key(handle, 0x54)  # Open Minecraft chat with T.
        time.sleep(.3)
        for char in '/' + text:
            user.PostMessageW(handle, 0x102, ord(char), 1)
            time.sleep(.012)
        key(handle, 0x0D)
        time.sleep(.7)
    def click(handle, right=False):
        user.PostMessageW(handle, 0x204 if right else 0x201, 2 if right else 1, 0)
        time.sleep(.15)
        user.PostMessageW(handle, 0x205 if right else 0x202, 0, 0)
        time.sleep(1)
    for index, record in enumerate(records):
        handle = windows[record['pid']]
        user.MoveWindow(handle, (index % 2) * (width // 2), (index // 2) * (height // 2), width // 2, height // 2, True)
        x = 24 + index * 3
        command(handle, f'tp @s {x + .5} -60 0.5 0 0')
        user.PostMessageW(handle, 0x100, 0x57, 1 | (user.MapVirtualKeyW(0x57, 0) << 16))
        time.sleep(1)
        user.PostMessageW(handle, 0x101, 0x57, 1 | (user.MapVirtualKeyW(0x57, 0) << 16) | (3 << 30))
        command(handle, 'clear @s')
        command(handle, 'give @s dirt 32')
        command(handle, f'tp @s {x + .5} -60 0.5 0 26.57')
        command(handle, f'setblock {x} -60 2 stone')
        command(handle, f'setblock {x} -60 3 stone')
        click(handle)
        command(handle, f'tp @s {x + .5} -60 0.5 0 29.3')
        click(handle, right=True)
        print('Vanilla client window operations:', record['name'], flush=True)
    if capture:
        from PIL import ImageGrab
        ImageGrab.grab().save(ARTIFACTS / 'gui-four-clients.png')


def respawn_window(record):
    """Activate the unmodified Minecraft death-screen respawn control."""
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.SetProcessDPIAware()
    handle = None
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(window, _):
        nonlocal handle
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value == record['pid'] and user.IsWindowVisible(window):
            handle = window
        return True
    user.EnumWindows(callback_type(visit), 0)
    if not handle:
        raise RuntimeError('Vanilla client window disappeared during respawn')
    rect = wintypes.RECT()
    if not user.GetClientRect(handle, ctypes.byref(rect)):
        raise RuntimeError('Cannot inspect vanilla client window')
    x, y = (rect.right - rect.left) // 2, int((rect.bottom - rect.top) * .54)
    point = y << 16 | x
    for key in (0x09, 0x0D):
        scan = user.MapVirtualKeyW(key, 0)
        user.PostMessageW(handle, 0x100, key, 1 | (scan << 16))
        time.sleep(.15)
        user.PostMessageW(handle, 0x101, key, 1 | (scan << 16) | (3 << 30))
        time.sleep(.2)
    # GLFW tracks the pointer from WM_MOUSEMOVE; button messages alone leave
    # its cursor position stale, so a click can miss the respawn control.
    user.PostMessageW(handle, 0x200, 0, point)
    time.sleep(.15)
    user.PostMessageW(handle, 0x201, 1, point)
    time.sleep(.1)
    user.PostMessageW(handle, 0x202, 0, point)


def forward(transport, target):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel = transport.open_channel('direct-tcpip', ('127.0.0.1', target), self.request.getpeername())
            try:
                while True:
                    ready, _, _ = select.select([self.request, channel], [], [], 1)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (channel if source is self.request else self.request).sendall(data)
            except (OSError, EOFError):
                pass
            finally:
                channel.close()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True
    server = Server(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fetch_tree(transport, remote, local):
    local.mkdir(parents=True, exist_ok=True)
    with transport.open_sftp_client() as sftp:
        def copy_directory(source, destination):
            for entry in sftp.listdir_attr(source):
                remote_path = source.rstrip('/') + '/' + entry.filename
                local_path = destination / entry.filename
                if stat.S_ISDIR(entry.st_mode):
                    local_path.mkdir(exist_ok=True)
                    copy_directory(remote_path, local_path)
                elif entry.filename.endswith('.jsonl'):
                    sftp.get(remote_path, str(local_path))
        copy_directory(remote, local)


async def endpoint(cls, profile, endpoints, **kwargs):
    item = cls(profile, announce=False, transport_policy='relay', **kwargs)
    ready = asyncio.Event()
    item.on_status = lambda state, _detail: ready.set() if state == 'online' else None
    task = asyncio.create_task(item.run())
    endpoints.append((item, task))
    await asyncio.wait_for(ready.wait(), 25)
    return item


async def main():
    ARTIFACTS.mkdir(parents=True)
    logging.basicConfig(filename=ARTIFACTS / 'transport.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s', encoding='utf-8')
    report = dict(started=time.strftime('%Y-%m-%d %H:%M:%S'), version='1.21.8',
                  node='Ubuntu 24.04 native C++', transport='relay', passed=False, clients=[])
    processes, endpoints, logs = [], [], []
    ssh = remote_in = remote_out = tunnel = server = observer = None
    remote_logs = None
    gui = load_gui()
    try:
        ssh = await asyncio.to_thread(connect)
        remote_logs = '/tmp/buggcraft-native-validation/vanilla-player-logs-' + ARTIFACTS.name
        remote_in, remote_out, remote_err = await asyncio.to_thread(ssh.exec_command,
            'cd /tmp/buggcraft-native-validation && .venv/bin/python scripts/benchmark_server.py native --workers 4 --diagnostics-dir ' + remote_logs)
        profile_data = json.loads(await asyncio.wait_for(asyncio.to_thread(remote_out.readline), 25))
        tunnel = forward(ssh.get_transport(), profile_data['port'])
        profile = RelayProfile('127.0.0.1', tunnel.server_address[1], profile_data['token'], profile_data['key'])
        world = ARTIFACTS / 'world'
        world.mkdir()
        # A fresh flat world; no existing player worlds or launcher profiles are touched.
        import socket
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            game_port = reservation.getsockname()[1]
        (world / 'eula.txt').write_text('eula=true\n', encoding='utf-8')
        (world / 'server.properties').write_text(
            f'server-ip=127.0.0.1\nserver-port={game_port}\nonline-mode=false\nenforce-secure-profile=false\n'
            'gamemode=creative\nforce-gamemode=true\ndifficulty=peaceful\nlevel-type=minecraft:flat\n'
            'generator-settings={"biome":"minecraft:plains","layers":[{"block":"minecraft:bedrock","height":1},'
            '{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}]}\n'
            'spawn-protection=0\nview-distance=4\nsimulation-distance=4\nmax-players=12\n'
            'network-compression-threshold=256\nallow-flight=true\n', encoding='utf-8')
        def offline_uuid(name):
            return str(uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + name).encode()).digest(), version=3))
        (world / 'ops.json').write_text(json.dumps([dict(uuid=offline_uuid(name), name=name,
            level=4, bypassesPlayerLimit=True) for name in NAMES]), encoding='utf-8')
        server = await asyncio.create_subprocess_exec(str(JAVA), '-Xms256M', '-Xmx1G', '-jar',
            str(WORKSPACE / 'multiplayer-tests/game-server/server.jar'), 'nogui', cwd=world,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000)
        lines, game_ready = [], asyncio.Event()
        async def watch_server():
            with (ARTIFACTS / 'server.log').open('w', encoding='utf-8') as output:
                while line := await server.stdout.readline():
                    text = line.decode(errors='replace')
                    lines.append(text)
                    output.write(text)
                    output.flush()
                    if 'Done (' in text:
                        game_ready.set()
        server_watch = asyncio.create_task(watch_server())
        await asyncio.wait_for(game_ready.wait(), 100)
        server.stdin.write(b'gamerule doDaylightCycle false\ntime set day\ngamerule doMobSpawning false\n')
        await server.stdin.drain()
        host = await endpoint(TunnelHost, profile, endpoints, game_port=game_port)
        clients = [await endpoint(TunnelClient, profile, endpoints, invite=host.invite, listen_port=0) for _ in NAMES]
        report['compression'] = [type(client.listener).__name__ for client in clients]
        minecraft = next(WORKSPACE.glob('PCL */.minecraft'))
        records, commands = [], []
        for name, client in zip(NAMES, clients):
            game = ARTIFACTS / name
            game.mkdir()
            (game / 'options.txt').write_text('lang:en_us\nrenderDistance:4\nsimulationDistance:4\n'
                'maxFps:20\nguiScale:2\nautoJump:false\npauseOnLostFocus:false\nsoundCategory_master:0.0\n'
                'onboardAccessibility:false\n', encoding='utf-8')
            options = dict(username=name, uuid=offline_uuid(name), token='0', executablePath=str(JAVA),
                jvmArguments=['-Xms256M', '-Xmx1G'], gameDirectory=str(game),
                launcherName='BuggcraftNativeValidation', launcherVersion='1', customResolution=True,
                resolutionWidth='800', resolutionHeight='450', quickPlayMultiplayer=f'127.0.0.1:{client.listen_port}')
            command = minecraft_launcher_lib.command.get_minecraft_command('1.21.8', str(minecraft), options)
            commands.append(command)
            output = (game / 'launch.log').open('w', encoding='utf-8')
            logs.append(output)
            process = await asyncio.create_subprocess_exec(*command, cwd=game, stdout=output, stderr=output,
                                                          creationflags=0x08000000)
            processes.append(process)
            records.append(dict(name=name, pid=process.pid, port=client.listen_port, game_directory=str(game)))
            (ARTIFACTS / 'gui-processes.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
            print('Vanilla client started:', name, flush=True)
            await asyncio.sleep(8)
        deadline = time.monotonic() + 150
        while not all(any(name + ' joined the game' in line for line in lines) for name in NAMES):
            if time.monotonic() > deadline:
                raise TimeoutError('Four vanilla clients did not all join; inspect per-client and server logs')
            if any(process.returncode is not None for process in processes):
                raise RuntimeError('A vanilla client exited before joining')
            await asyncio.sleep(1)
        report['clients'] = records
        print('All four vanilla clients joined through Ubuntu C++ relay', flush=True)
        observer_client = await endpoint(TunnelClient, profile, endpoints, invite=host.invite, listen_port=0)
        observer_log = (ARTIFACTS / 'observer.log').open('w', encoding='utf-8')
        logs.append(observer_log)
        env = dict(os.environ, NODE_PATH=str(WORKSPACE / 'multiplayer-tests/node_modules'))
        observer = await asyncio.create_subprocess_exec('node', str(ROOT / 'scripts/vanilla_observer.cjs'),
            str(observer_client.listen_port), str(ARTIFACTS / 'operations.json'), stdout=observer_log,
            stderr=observer_log, env=env, creationflags=0x08000000)
        await asyncio.sleep(12)
        await asyncio.to_thread(operate_windows, records)
        async def server_command(command, predicate, timeout=12):
            start = len(lines)
            server.stdin.write((command + '\n').encode())
            await server.stdin.drain()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if any(predicate(line) for line in lines[start:]):
                    return next(line for line in lines[start:] if predicate(line))
                if server.returncode is not None:
                    raise RuntimeError('Vanilla server exited during death/respawn validation')
                await asyncio.sleep(.05)
            raise TimeoutError(f'Vanilla server did not confirm command: {command}')

        death_respawn = []
        for record in records[1:]:
            name = record['name']
            death_start = len(lines)
            await server_command(f'kill {name}', lambda line: name in line and
                                 any(word in line.lower() for word in ('killed', 'died', 'slain')))
            from PIL import ImageGrab
            ImageGrab.grab().save(ARTIFACTS / f'death-screen-{name}.png')
            killed_at = time.perf_counter()
            health_samples = []
            for attempt in range(6):
                await asyncio.to_thread(respawn_window, record)
                response = await server_command(f'data get entity {name} Health',
                    lambda line: name in line and 'following entity data' in line)
                match = re.search(r'following entity data:\s*(-?\d+(?:\.\d+)?)f?', response)
                health = float(match.group(1)) if match else None
                health_samples.append(health)
                report.setdefault('death_respawn_attempts', []).append(
                    dict(player=name, attempt=attempt + 1, health=health))
                if health is not None and health > 0:
                    death_respawn.append(dict(player=name, attempts=attempt + 1,
                                              seconds=round(time.perf_counter() - killed_at, 2),
                                              health=health, health_samples=health_samples))
                    break
            else:
                raise AssertionError(f'{name} did not leave the death screen; see server and client logs')
            if any(name in line and 'lost connection' in line.lower() for line in lines[death_start:]):
                raise AssertionError(f'{name} disconnected during death/respawn')
        report['death_respawn'] = death_respawn
        await asyncio.wait_for(observer.wait(), 120)
        operations = json.loads((ARTIFACTS / 'operations.json').read_text(encoding='utf-8'))
        if not operations.get('passed'):
            raise AssertionError('Real client block synchronization failed; inspect operations.json')
        report['operations'] = operations
        for record in records:
            content = (Path(record['game_directory']) / 'logs/latest.log').read_text(encoding='utf-8', errors='replace')
            if not all(f'[{name}: Changed the block at' in content for name in NAMES if name != record['name']):
                raise AssertionError('A vanilla client did not receive the other clients\' block updates')
        report['cross_client_chat_sync'] = True
        # Restart one real client, retaining its offline UUID and its game data.
        old_joins = sum(NAMES[-1] + ' joined the game' in line for line in lines)
        processes[-1].terminate()
        await processes[-1].wait()
        await asyncio.sleep(2)
        processes[-1] = await asyncio.create_subprocess_exec(*commands[-1], cwd=records[-1]['game_directory'],
            stdout=logs[3], stderr=logs[3], creationflags=0x08000000)
        records[-1]['rejoined_pid'] = processes[-1].pid
        deadline = time.monotonic() + 90
        while sum(NAMES[-1] + ' joined the game' in line for line in lines) <= old_joins:
            if time.monotonic() > deadline:
                raise TimeoutError('Restarted vanilla client did not rejoin')
            await asyncio.sleep(1)
        report['real_client_rejoin'] = True
        # Verify a stable multiplayer period after the client interactions.
        await asyncio.sleep(30)
        if any(process.returncode is not None for process in processes):
            raise RuntimeError('A vanilla client exited during multiplayer soak')
        remote_in.write('stats\n')
        remote_in.flush()
        report['node_payload_counters'] = json.loads(await asyncio.to_thread(remote_out.readline))
        report['passed'] = True
    except Exception as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        for process in ([observer] if observer else []) + processes:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        for item, _task in reversed(endpoints):
            await item.stop()
        await asyncio.gather(*(task for _item, task in endpoints), return_exceptions=True)
        if server and server.returncode is None:
            server.stdin.write(b'stop\n')
            await server.stdin.drain()
            await asyncio.wait_for(server.wait(), 25)
            await server_watch
        if remote_in:
            remote_in.write('stop\n')
            remote_in.flush()
            await asyncio.to_thread(remote_in.channel.recv_exit_status)
        if ssh and remote_logs:
            node_logs = ARTIFACTS / 'node-players'
            await asyncio.to_thread(fetch_tree, ssh.get_transport(), remote_logs, node_logs)
            players = {}
            for path in node_logs.glob('*/*.jsonl'):
                entries = [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines()]
                players.setdefault(path.parent.name, []).extend(entries)
            report['node_player_logs'] = {
                'players': len(players),
                'sessions': sum(1 for path in node_logs.glob('*/*.jsonl')),
                'stream_samples': sum(row.get('event') == 'sample' for rows in players.values() for row in rows),
                'stream_closures': sum(row.get('event') == 'stream_closed' for rows in players.values() for row in rows),
            }
        if tunnel:
            await asyncio.to_thread(tunnel.shutdown)
            tunnel.server_close()
        if ssh:
            ssh.close()
        for output in logs:
            output.close()
        compression = re.findall(r'relay_compression trace=\S+ codec=zstd-1 raw_bytes=(\d+) wire_bytes=(\d+)',
                                 (ARTIFACTS / 'transport.log').read_text(encoding='utf-8', errors='replace'))
        raw = sum(int(item[0]) for item in compression)
        wire = sum(int(item[1]) for item in compression)
        report['compression_measurement'] = dict(raw_bytes=raw, framed_bytes=wire,
            reduction_percent=round(100*(1-wire/raw), 2) if raw else 0,
            scope='Both endpoint directions; excludes SSH/TCP and administrative test tunnel overhead')
        report['completed'] = time.strftime('%Y-%m-%d %H:%M:%S')
        (ARTIFACTS / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Vanilla validation artifacts:', ARTIFACTS, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
