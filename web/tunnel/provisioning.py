"""Generate independent community node secrets and a pinned launcher profile."""
import json
import os
from pathlib import Path
import secrets
from datetime import datetime, timedelta, timezone
import ipaddress
import asyncssh

from tunnel.portal import password_hash
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def initialize(directory, host, port, *, name='Buggcraft 社区节点', web_port=18443,
               entry_host=None, entry_port=None, admin_username='admin',
               admin_password=None, access_password=None, capacity=None,
               public_join=False):
    base = Path(directory).resolve()
    base.mkdir(parents=True, exist_ok=True)
    names = ('server.conf', 'relay.token', 'relay_host_key', 'relay-profile.json',
             'node-state.json', 'admin-credentials.txt', 'web-cert.pem', 'web-key.pem')
    if any((base / name).exists() for name in names):
        raise ValueError('Refusing to overwrite existing node configuration or keys')
    if (not host or len(host) > 253 or any(c.isspace() or c in '/\\@?#' for c in host)
            or not 1 <= port <= 65535 or not 1 <= web_port <= 65535 or port == web_port):
        raise ValueError('A public hostname/IP and valid TCP port are required')
    if (not admin_username or len(admin_username) > 32
            or not all(c.isascii() and (c.isalnum() or c in '-_') for c in admin_username)):
        raise ValueError('Admin username must use ASCII letters, numbers, hyphen or underscore')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('Node name must contain 1–80 characters')
    from tunnel.capacity import policy
    capacity = policy(capacity)
    if type(public_join) is not bool:
        raise ValueError('Invalid public access policy')
    from tunnel.federation import validate_profile
    key = asyncssh.generate_private_key('ssh-ed25519')
    token = secrets.token_urlsafe(48)
    chosen_password = admin_password is not None
    admin_password = secrets.token_urlsafe(20) if admin_password is None else admin_password
    access_password = access_password or secrets.token_urlsafe(18)
    if len(admin_password) < 12:
        raise ValueError('Admin password must contain at least 12 characters')
    tls_key = ec.generate_private_key(ec.SECP256R1())
    try:
        address = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        address = x509.DNSName(host)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64])])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                   .add_extension(x509.SubjectAlternativeName([address, x509.DNSName('localhost'),
                                                              x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                   .sign(tls_key, hashes.SHA256()))
    profile = {'server_host': host if entry_host is None else entry_host,
               'server_port': port if entry_port is None else entry_port, 'token': token,
               'server_public_key': key.export_public_key().decode()}
    validate_profile(profile)
    console_host = '[' + host + ']' if ':' in host else host
    values = {'relay.token': token, 'relay_host_key': key.export_private_key().decode(),
        'relay-profile.json': json.dumps(profile, indent=2),
        'node-state.json': json.dumps({'name': name, 'admin_user': admin_username,
                                      'admin_hash': password_hash(admin_password),
                                      'access_hash': password_hash(access_password),
                                      'public_join': public_join, 'draining': False,
                                      'capacity': capacity,
                                      'registration_open': False, 'blocked_accounts': [],
                                      'network_key': secrets.token_urlsafe(48),
                                      'seeds': [], 'blocked': []}, ensure_ascii=False, indent=2),
        'admin-credentials.txt': f'Console: https://{console_host}:{web_port}/\nUsername: {admin_username}\n'
                                 f'Admin password: {"(chosen during setup)" if chosen_password else admin_password}\n'
                                 f'Player access password: {access_password}\n'
                                 f'TLS certificate SHA256: {certificate.fingerprint(hashes.SHA256()).hex()}\n',
        'web-cert.pem': certificate.public_bytes(serialization.Encoding.PEM).decode(),
        'web-key.pem': tls_key.private_bytes(serialization.Encoding.PEM,
                                            serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode(),
        'server.conf': f'[relay]\nhost = 0.0.0.0\nport = {port}\ntoken_file = relay.token\n'
                      'host_key = relay_host_key\nmax_connections = 512\nmax_rooms = 128\n'
                      'max_streams_per_room = 128\nmax_total_streams = 512\nmax_streams_per_client = 8\n'
                      f'\n[web]\nhost = 0.0.0.0\nport = {web_port}\nstate_file = node-state.json\n'
                      'profile_file = relay-profile.json\ntls_cert = web-cert.pem\ntls_key = web-key.pem\n'}
    created = []
    try:
        for name, content in values.items():
            path = base / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return base
