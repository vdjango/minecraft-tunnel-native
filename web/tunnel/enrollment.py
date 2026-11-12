"""Installation-time bootstrap; node identity/proof still belong to ManagedControl."""
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import tempfile

from aiohttp import ClientSession, ClientTimeout, TCPConnector

from .managed_control import control_origin
from .provisioning import initialize


async def fetch_profile(origin, ca_file=None):
    """Bounded HTTPS download with system/private CA trust, never redirects."""
    origin = control_origin(origin)
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    async with ClientSession(connector=TCPConnector(ssl=context, limit=1),
                             timeout=ClientTimeout(total=15), trust_env=False) as session:
        async with session.post(origin + '/v1/enrollment/profile', json={}, allow_redirects=False) as reply:
            if reply.status != 200:
                raise ValueError('Node enrollment is unavailable at this control service')
            raw = await reply.content.read(4097)
            if len(raw) > 4096:
                raise ValueError('Enrollment profile exceeds limit')
            value = json.loads(raw)
    if (not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != 1 or
            not isinstance(value.get('signing_public_key'), str) or
            not re.fullmatch('[0-9a-f]{64}', value['signing_public_key']) or
            not isinstance(value.get('credential'), str) or not 32 <= len(value['credential']) <= 512):
        raise ValueError('Invalid enrollment profile')
    return value


async def join(directory, origin, host, *, port=3333, proof_port=18443, ca_file=None,
               region='cn-east', operator='community', slots=32, bandwidth=6000000):
    """Fetch trust then invoke the existing atomic initializer; no service side effects."""
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast or getattr(address, 'ipv4_mapped', None):
        raise ValueError('Use the public numeric IP of this node')
    if not 1 <= slots <= 2048 or not 1 <= bandwidth <= 1000000000:
        raise ValueError('Invalid slots or bandwidth (1-2048 slots, up to 1000 Mbps)')
    if not 1 <= len(region) <= 64 or not 1 <= len(operator) <= 64:
        raise ValueError('Region/operator must contain 1-64 characters')
    base = Path(directory).resolve()
    if (base / 'server.conf').exists():
        raise ValueError('Node already configured; use serve/status. A different control requires a new data directory.')
    origin = control_origin(origin)
    profile = await fetch_profile(origin, ca_file)
    endpoint_host = '[' + str(address) + ']' if address.version == 6 else str(address)
    with tempfile.TemporaryDirectory(prefix='buggcraft-enrollment-') as temporary:
        credential_file = Path(temporary) / 'enrollment'
        descriptor = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            target.write(profile['credential'])
        initialize(base, str(address), port, web_port=proof_port, control={
            'origin': origin, 'ca_file': str(Path(ca_file).resolve()) if ca_file else '',
            'signing_public_key': profile['signing_public_key'], 'credential_file': str(credential_file),
            'endpoint': f'{endpoint_host}:{port}', 'proof_endpoint': f'{endpoint_host}:{proof_port}',
            'region': region, 'operator': operator, 'slots': str(slots), 'bandwidth': str(bandwidth)})
    if os.name == 'posix':
        base.chmod(0o700)
    return base
