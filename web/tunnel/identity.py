"""Shared signed identity format; no network or gossip runtime."""
import hashlib
import hmac
import json
import asyncssh


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def identity(public_key):
    key = asyncssh.import_public_key(public_key).export_public_key().decode().strip()
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def validate_profile(value):
    if not isinstance(value, dict) or set(value) != {'server_host', 'server_port', 'token', 'server_public_key'}:
        raise ValueError('Invalid node profile')
    host, port, token = value['server_host'], value['server_port'], value['token']
    if (not isinstance(host, str) or not host or len(host) > 253 or
            any(c.isspace() or c in '/\\@?#' for c in host) or
            type(port) is not int or not 1 <= port <= 65535 or
            not isinstance(token, str) or not 32 <= len(token) <= 2048):
        raise ValueError('Invalid node address or credential')
    asyncssh.import_public_key(value['server_public_key'])
    return dict(value)


class AccountIssuer:
    """Persisted issuer identity on one event loop; no sockets or background tasks."""
    def __init__(self, key, profile, *, network_key, name='Community node', blocked=()):
        self.key = key
        self.profile = validate_profile(profile)
        self.id = identity(profile['server_public_key'])
        if not isinstance(network_key, str) or len(network_key) < 32:
            raise ValueError('Invalid identity audience key')
        self.network_key = network_key
        self.name = name[:80]
        self.blocked = set(blocked)
        self.region = ''
        self.accounts = None
        self.relay = None

    def proof(self, node_id):
        return hmac.new(self.network_key.encode(), node_id.encode(), hashlib.sha256).hexdigest()

