"""Run INSIDE a disposable Ubuntu container after apt installs the .deb.

Uses only the installed application, never a checkout or development venv.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = Path('/usr/lib/buggcraft-node')
DATA = Path('/var/lib/buggcraft-node')
sys.path.insert(0, str(APP / 'python'))
import asyncssh


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_until(fn):
    until = time.monotonic() + 25
    while True:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError):
            pass
        if time.monotonic() > until:
            raise AssertionError('Installed service failed to become ready')
        time.sleep(.05)


async def check_native(profile):
    async with asyncssh.connect(profile['server_host'], profile['server_port'],
            username='directory:rooms', password=json.dumps({'token': profile['token']}),
            known_hosts=([asyncssh.import_public_key(profile['server_public_key'])], [], []),
            client_keys=[], agent_path=None, config=None) as conn:
        async with conn.create_process(subsystem='buggcraft-rooms-v1') as process:
            process.stdin.write('{"op":"ping"}\n')
            assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))['ok']


def main():
    web_port, relay_port = port(), port()
    context = ssl._create_unverified_context()
    base = f'https://127.0.0.1:{web_port}'
    def request(path, data=None, headers=None):
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        return urllib.request.urlopen(req, context=context, timeout=5)
    def start():
        return subprocess.Popen(['runuser', '-u', 'buggcraft-node', '--', '/usr/bin/buggcraft-node',
            'serve', '--directory', str(DATA), '--setup-host', '127.0.0.1',
            '--web-port', str(web_port), '--no-browser'], stdout=log, stderr=log, start_new_session=True)
    process = None
    with Path(os.environ.get('BUGGCRAFT_PACKAGE_LOG', '/tmp/buggcraft-debian-smoke.log')).open('w') as log:
        try:
            process = start()
            link = wait_until(lambda: json.loads((DATA / 'setup-link.json').read_text()))
            assert (DATA / 'setup-link.json').stat().st_mode & 0o777 == 0o600
            output = subprocess.check_output(['/usr/bin/buggcraft-node', 'setup'], text=True)
            assert link['url'] in output
            with socket.create_connection(('127.0.0.1', web_port)) as raw:
                with context.wrap_socket(raw, server_hostname='localhost') as tls:
                    assert hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest() == link['fingerprint']
            try:
                request('/')
                raise AssertionError('Unauthenticated setup page accepted')
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            token = link['url'].split('token=', 1)[1]
            with request('/?token=' + token) as response:
                assert response.status == 200
            password = secrets.token_urlsafe(24)
            fields = dict(host='127.0.0.1', port=relay_port, web_port=web_port, name='Package validation',
                username='node_admin', password=password, password_confirm=password, public_join=True,
                max_rooms=7, room_capacity=11, bandwidth_mb_s=2)
            with request('/setup', json.dumps(fields).encode(),
                    {'Content-Type': 'application/json', 'X-Setup-Token': token}) as response:
                assert json.load(response)['complete']
            wait_until(lambda: request('/api/status').status == 200)
            assert not (DATA / 'setup-link.json').exists()
            profile = json.loads((DATA / 'relay-profile.json').read_text())
            asyncio.run(check_native(profile))
            # A real package reinstall must preserve identity, accounts and logs.
            identity_hash = hashlib.sha256((DATA / 'relay_host_key').read_bytes()).hexdigest()
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
            subprocess.run(['apt-get', 'install', '-y', '--reinstall', '/packages/' + os.environ['BUGGCRAFT_DEB_NAME']],
                           check=True, stdout=log, stderr=log)
            process = start()
            wait_until(lambda: request('/api/status').status == 200)
            assert hashlib.sha256((DATA / 'relay_host_key').read_bytes()).hexdigest() == identity_hash
            assert not (DATA / 'setup-link.json').exists()
            assert 'token=' not in subprocess.check_output(['/usr/bin/buggcraft-node', 'setup'], text=True)
            asyncio.run(check_native(profile))
            subprocess.run(['systemd-analyze', 'verify', '/usr/lib/systemd/system/buggcraft-node.service'], check=True)
            print('DEBIAN_SMOKE ' + json.dumps(dict(passed=True, apt_install=True, apt_reinstall=True,
                private_setup_link=True, certificate_fingerprint=True, browser_setup=True,
                native_ssh=True, restart_preserves_identity=True, unit_verified=True,
                scope='Ubuntu container; service started as dedicated user, not a systemd PID 1 boot')))
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
