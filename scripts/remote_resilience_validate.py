"""Upload source only to the isolated Ubuntu validation checkout and retain results."""
import argparse
from pathlib import Path
import shlex
import sys
import time

from remote_validate import connect, ROOT
from remote_bridge_soak import fetch

REMOTE = '/tmp/buggcraft-native-validation'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('all','build','tests','routes','web','scale','chain'), default='all')
    parser.add_argument('--skip-upload', action='store_true', help='Use already uploaded sources without changing a running checkout')
    args = parser.parse_args()
    name = time.strftime('resilience-%Y%m%dT%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    artifact = ROOT / 'validation-artifacts' / name
    artifact.mkdir(parents=True)
    remote_artifact = REMOTE + '/soak/' + name
    sources = [ROOT/'CMakeLists.txt']
    for folder in ('core','bridge','tests','compatibility/tests','web/tunnel','scripts'):
        sources.extend(p for p in (ROOT/folder).rglob('*')
                       if p.is_file() and p.suffix in ('.cpp','.hpp','.py','.js','.html','.css') and '__pycache__' not in p.parts)
    # The portal renders this document into /developers; keep the isolated
    # checkout equivalent to a release source tree for governance tests.
    sources.append(ROOT/'PROTOCOL_STANDARD.md')
    print('RESILIENCE_ARTIFACTS ' + str(artifact), flush=True)
    with connect() as client:
        client.get_transport().set_keepalive(20)
        if not args.skip_upload:
            with client.open_sftp() as sftp:
                for path in sources:
                    sftp.put(str(path), REMOTE + '/' + path.relative_to(ROOT).as_posix())
                launcher=ROOT.parent/'buggcraft/src/buggcraft/hookup'
                for folder in (REMOTE+'/launcher',REMOTE+'/launcher/hookup'):
                    try:sftp.mkdir(folder)
                    except IOError:pass
                for name in ('__init__.py','native_routes.py','relay.py','rooms.py','nodes.py'):
                    sftp.put(str(launcher/name),REMOTE+'/launcher/hookup/'+name)
        commands = ['cd '+shlex.quote(REMOTE), 'mkdir -p '+shlex.quote(remote_artifact),
            'export BUGGCRAFT_NATIVE_CORE='+REMOTE+'/build/buggcraft-core',
            'export BUGGCRAFT_BRIDGE_EXECUTABLE='+REMOTE+'/build/buggcraft-bridge',
            'export BUGGCRAFT_LAUNCHER_SDK='+REMOTE+'/launcher',
            'export BUGGCRAFT_BRIDGE_SOAK_LOG_DIR='+remote_artifact+'/player-logs',
            'export BUGGCRAFT_CHAIN_REPORT='+remote_artifact+'/chain.json',
            'export PYTHONUNBUFFERED=1', 'set -e',
            'test -e compatibility/src || ln -s ../web compatibility/src']
        if args.phase in ('all','build','routes','chain'):
            commands.append('cmake --build build --parallel 2 > '+remote_artifact+'/build.log 2>&1')
        if args.phase == 'routes':
            commands.extend(['ctest --test-dir build -V',
                '.venv/bin/python -m unittest tests.test_route_selection tests.test_node_health -v'])
        if args.phase in ('all','tests'):
            commands.extend(['ctest --test-dir build --output-on-failure',
                '.venv/bin/python -m unittest tests.test_bridge tests.test_route_selection tests.test_node_health tests.test_native tests.test_federation_resilience tests.test_federation_budget -v',
                '.venv/bin/python scripts/check_compatibility.py'])
        if args.phase in ('all','tests','web'):
            commands.append('.venv/bin/python scripts/check_compatibility.py test_portal_governance test_feedback test_web_accounts test_federation_integrity test_mail test_bandwidth test_onboarding')
        if args.phase in ('all','scale'):
            commands.append('.venv/bin/python scripts/federation_scale.py --nodes 32 --seconds 65 --output '+remote_artifact+'/scale.json')
        if args.phase in ('all','chain'):
            commands.append('.venv/bin/python -m unittest tests.test_federation_lookup tests.test_federation_chain tests.test_bridge.BridgeTests.test_compressed_tail_drains_after_remote_channel_close -v')
        channel = client.get_transport().open_session(timeout=20)
        channel.exec_command('\n'.join(commands)+' 2>&1')
        deadline = time.monotonic()+900
        with (artifact/'console.log').open('wb') as log:
            while True:
                if channel.recv_ready() or channel.recv_stderr_ready():
                    data = channel.recv(65536) if channel.recv_ready() else channel.recv_stderr(65536)
                    log.write(data)
                    log.flush()
                    sys.stdout.write(data.decode('utf-8',errors='replace'))
                    sys.stdout.flush()
                elif channel.exit_status_ready():
                    break
                elif time.monotonic()>deadline:
                    channel.close()
                    raise TimeoutError('Isolated resilience validation deadline exceeded')
                else:
                    time.sleep(.2)
        status = channel.recv_exit_status()
        with client.open_sftp() as sftp:
            fetch(sftp,remote_artifact,artifact)
    print('RESILIENCE_RETRIEVED '+str(dict(path=str(artifact),returncode=status)),flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
