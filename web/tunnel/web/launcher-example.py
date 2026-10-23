"""Read-only third-party launcher example. Install: python -m pip install asyncssh."""
import argparse
import asyncio
import base64
import getpass
import json

import asyncssh


def decode_profile(code):
    if not code.startswith('buggnode1-') or len(code) > 20000:
        raise ValueError('Invalid node code')
    value = code[len('buggnode1-'):]
    profile = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    if not isinstance(profile, dict) or set(profile) != {
            'server_host', 'server_port', 'token', 'server_public_key'}:
        raise ValueError('Invalid profile fields')
    if (not isinstance(profile['server_host'], str) or not profile['server_host'] or
            type(profile['server_port']) is not int or not 1 <= profile['server_port'] <= 65535 or
            not isinstance(profile['token'], str) or not 32 <= len(profile['token']) <= 2048):
        raise ValueError('Invalid profile values')
    return profile


async def inspect(profile, identifier=None):
    public = asyncssh.import_public_key(profile['server_public_key'])
    async with asyncssh.connect(
        profile['server_host'], profile['server_port'], username='directory:rooms',
        password=json.dumps({'token': profile['token'].split('~', 1)[0]}), known_hosts=([public], [], []),
        client_keys=[], agent_path=None, preferred_auth=['password'], config=None,
        x509_trusted_certs=None, x509_trusted_cert_paths=[],
        connect_timeout=3, login_timeout=5, encoding=None,
    ) as connection:
        async def request(operation, **fields):
            async with connection.create_process(subsystem='buggcraft-rooms-v1', encoding=None) as process:
                process.stdin.write(json.dumps(dict(op=operation, **fields)).encode() + b'\n')
                response = bytearray()
                async with asyncio.timeout(10):
                    while not response.endswith(b'\n'):
                        chunk = await process.stdout.read(16384)
                        if not chunk or len(response) + len(chunk) > 65536:
                            raise ValueError('Invalid response size or EOF')
                        response.extend(chunk)
                value = json.loads(response)
                if not value.get('ok'):
                    raise ValueError(value.get('error', 'Request rejected'))
                return value['result']
        ping = await request('ping')
        if ping.get('version') != 1:
            raise ValueError('Unsupported protocol version')
        result = {'node': ping['name'], 'version': ping['version'],
                  'capabilities': ping.get('capabilities', []),
                  'page': await request('list', offset=0, limit=20, query='')}
        if identifier:
            if 'account-lookup-v1' not in ping.get('capabilities', []):
                raise ValueError('Node does not support account lookup')
            result['accounts'] = (await request('account_lookup', identifier=identifier))['accounts']
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identifier', help='Exact username or full account ID')
    args = parser.parse_args()
    try:
        profile = decode_profile(getpass.getpass('Node code (hidden): ').strip())
        print(json.dumps(asyncio.run(inspect(profile, args.identifier)), ensure_ascii=True, indent=2))
    except (ValueError, OSError, asyncssh.Error, TimeoutError):
        raise SystemExit('Connection or validation failed. Check the trusted code and node availability.')
