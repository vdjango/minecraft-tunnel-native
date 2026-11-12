"""Fresh apt install/reinstall validation using only the installed managed node."""
import asyncio
import configparser
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

APP = Path('/usr/lib/buggcraft-node')
DATA = Path('/var/lib/buggcraft-node')
sys.path[:0] = [str(APP/'python'), str(APP/'web')]
import asyncssh
from cryptography.hazmat.primitives import serialization
from tunnel.provisioning import initialize


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


def main():
    relay_port, proof_port = port(), port()
    process = None
    with tempfile.TemporaryDirectory() as temporary, Path('/tmp/buggcraft-debian-smoke.log').open('w') as log:
        trust = Path(temporary)
        initialize(trust, '127.0.0.1', 3333)
        profile = dict(protocol=1, signing_public_key='11'*32, credential='install-enrollment-'+'x'*48)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != '/v1/enrollment/profile':
                    self.send_error(503)
                    return
                body = json.dumps(profile).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        center = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(trust/'web-cert.pem', trust/'web-key.pem')
        center.socket = tls.wrap_socket(center.socket, server_side=True)
        thread = threading.Thread(target=center.serve_forever, daemon=True)
        thread.start()
        def start():
            return subprocess.Popen(['runuser', '-u', 'buggcraft-node', '--', '/usr/bin/buggcraft-node',
                'serve'], stdout=log, stderr=log, start_new_session=True)
        def status():
            with urllib.request.urlopen(f'https://127.0.0.1:{proof_port}/api/status',
                    context=ssl.create_default_context(cafile=str(DATA/'web-cert.pem')), timeout=3) as reply:
                return json.load(reply)
        try:
            assert not (DATA/'server.conf').exists()
            subprocess.run(['/usr/bin/buggcraft-node', 'join', '--host', '127.0.0.1', '--control',
                f'https://127.0.0.1:{center.server_port}', '--ca-file', str(trust/'web-cert.pem'),
                '--port', str(relay_port), '--proof-port', str(proof_port), '--no-start'], check=True)
            assert not (APP/'web/tunnel/web').exists()
            assert not (DATA/'admin-credentials.txt').exists()
            assert not (DATA/'relay-profile.json').exists()
            assert (DATA/'relay_host_key').stat().st_mode & 0o777 == 0o600
            assert (DATA/'relay_host_key').stat().st_uid != 0
            identity_hash = hashlib.sha256((DATA/'relay_host_key').read_bytes()).hexdigest()
            process = start()
            state = wait_until(status)
            assert state['role'] == 'relay' and state['launcher_login'] == 0
            assert state['control_status'] != 'ready'  # This fixture only supplies the bootstrap profile.
            with socket.create_connection(('127.0.0.1', relay_port), timeout=5) as connection:
                assert b'SSH-2.0-' in connection.recv(256)
            context = ssl.create_default_context(cafile=str(DATA/'web-cert.pem'))
            endpoint = f'127.0.0.1:{relay_port}'
            nonce = 'fresh-install-proof-nonce'
            request = urllib.request.Request(f'https://127.0.0.1:{proof_port}/.well-known/buggcraft-node-proof',
                json.dumps(dict(nonce=nonce, endpoint=endpoint)).encode(), headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request, context=context, timeout=5) as reply:
                signature = bytes.fromhex(json.load(reply)['signature'])
            key = serialization.load_ssh_private_key((DATA/'relay_host_key').read_bytes(), password=None)
            key.public_key().verify(signature, ('buggcraft-node-proof-v1\n'+nonce+'\n'+endpoint).encode())
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
            subprocess.run(['apt-get', 'install', '-y', '--reinstall', '/packages/'+os.environ['BUGGCRAFT_DEB_NAME']],
                           check=True, stdout=log, stderr=log)
            process = start()
            wait_until(status)
            assert hashlib.sha256((DATA/'relay_host_key').read_bytes()).hexdigest() == identity_hash
            repeated = subprocess.run(['/usr/bin/buggcraft-node', 'join', '--host', '127.0.0.1', '--no-start'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert repeated.returncode != 0
            assert hashlib.sha256((DATA/'relay_host_key').read_bytes()).hexdigest() == identity_hash
            subprocess.run(['systemd-analyze', 'verify', '/usr/lib/systemd/system/buggcraft-node.service'], check=True)
            print('DEBIAN_SMOKE '+json.dumps(dict(passed=True, apt_install=True, apt_reinstall=True,
                managed_join=True, private_keys=True, signed_proof=True, native_ssh=True,
                restart_preserves_identity=True, no_node_web=True, unit_verified=True,
                scope='Disposable Ubuntu container; no systemd PID 1 or real control enrollment/load test')))
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            center.shutdown()
            center.server_close()
            thread.join()


if __name__ == '__main__':
    main()
