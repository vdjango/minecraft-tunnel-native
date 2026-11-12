"""Create a private expiring enrollment file through the existing admin login."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import aiohttp


async def administer(url, username, password, *, fingerprint=None, status=False, lifetime=600):
    """HTTPS only, normal CA verification or an explicit SHA-256 certificate pin."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Use the HTTPS control center origin without credentials or a path')
    tls = True
    if fingerprint is not None:
        if not re.fullmatch('[0-9a-fA-F]{64}', fingerprint):
            raise ValueError('Certificate SHA-256 must contain 64 hex digits')
        tls = aiohttp.Fingerprint(bytes.fromhex(fingerprint))
    url = url.rstrip('/')
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                    timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url + '/api/login', json={'username': username, 'password': password},
                                ssl=tls, allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError('Administrator login failed')
            csrf = (await response.json())['csrf']
        if status:
            request = session.get(url + '/api/control/status', ssl=tls, allow_redirects=False)
        else:
            request = session.post(url + '/api/control/invitations', json={'lifetime': lifetime},
                                   headers={'X-CSRF-Token': csrf}, ssl=tls, allow_redirects=False)
        async with request as response:
            if response.status != 200:
                raise RuntimeError('Control operation rejected; check mode, permissions and lifetime')
            return await response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='HTTPS control center origin')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--certificate-sha256', help='Optional pin for a private/self-signed certificate')
    parser.add_argument('--lifetime', type=int, default=600, help='Ticket lifetime in seconds (60–3600)')
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--output', type=Path, help='New private enrollment JSON; never overwritten')
    operation.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('Output already exists; choose a new private enrollment file')
    if not 60 <= args.lifetime <= 3600:
        parser.error('Lifetime must be 60–3600 seconds')
    try:
        result = asyncio.run(administer(args.url, args.username, getpass.getpass('Administrator password: '),
            fingerprint=args.certificate_sha256, status=args.status, lifetime=args.lifetime))
        if args.status:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                json.dump(result, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            print('Enrollment file written. Transfer privately and remove it after enrollment.')
    except (ValueError, RuntimeError, OSError, aiohttp.ClientError):
        # Transport errors can contain URLs; never print credential-bearing data.
        parser.exit(1, 'Control operation failed. Check HTTPS trust, administrator login and center availability.\n')


if __name__ == '__main__':
    main()
