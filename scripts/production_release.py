"""Stage and roll out the native node using the existing production SSH trust.

Never copies local production settings, credentials, or player data into a release.
Each activation backs up persistent data and automatically restores the old release
if the local health check fails. Both servers keep their existing systemd unit.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shlex
import tarfile
import textwrap

from production_remote import connect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = Path((ROOT / 'build/production-artifact.path').read_text().strip())
TAG = ARTIFACT.name.removeprefix('production-native-')
RELEASE = '/opt/buggcraft-node/releases/native-' + TAG


def command(client, cmd, timeout=240):
    _, out, err = client.exec_command(cmd, timeout=timeout)
    output = out.read().decode(errors='replace')
    error = err.read().decode(errors='replace')
    status = out.channel.recv_exit_status()
    if status:
        raise RuntimeError(f'Remote command failed ({status}): {error[-3000:]} {output[-3000:]}')
    return output


def remote_python(client, script):
    return command(client, 'sudo -n python3 -c ' + shlex.quote(textwrap.dedent(script)))


def save(name, value):
    (ARTIFACT / name).write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def prepare():
    with connect('node1') as client:
        command(client, 'tar -czf /tmp/buggcraft-native-runtime-production.tar.gz '
                '-C /tmp/buggcraft-native-production-runtime bin/buggcraft-core lib/libssh.so '
                'lib/libssh.so.4 lib/libssh.so.4.12.0 share')
        with client.open_sftp() as sftp:
            sftp.get('/tmp/buggcraft-native-runtime-production.tar.gz', str(ARTIFACT / 'runtime.tar.gz'))
    bundle = ARTIFACT / 'release.tar.gz'
    with tarfile.open(bundle, 'w:gz') as output:
        for directory, target in (('web', 'src'), ('site', 'site'), ('licenses', 'licenses')):
            output.add(ROOT / directory, arcname=target, filter=lambda info:
                       None if '__pycache__' in info.name or info.name.endswith('.pyc') or info.name.startswith('compatibility/src') else info)
        for name in ('requirements.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'PROTOCOL_STANDARD.md'):
            output.add(ROOT / name, arcname=name)
        with tarfile.open(ARTIFACT / 'runtime.tar.gz') as runtime:
            for item in runtime:
                output.addfile(item, runtime.extractfile(item) if item.isfile() else None)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    save('release-manifest.json', dict(tag=TAG, release=RELEASE, archive_sha256=digest,
         contents='src web portal, site, pinned requirements, Linux native core, libssh, licenses'))
    with tarfile.open(ARTIFACT / 'validation-source.tar.gz', 'w:gz') as output:
        for name in ('web', 'compatibility', 'scripts/check_compatibility.py', 'PROTOCOL_STANDARD.md'):
            output.add(ROOT / name, arcname=name, filter=lambda info:
                       None if '__pycache__' in info.name or info.name.endswith('.pyc') else info)

    def stage(node):
        with connect(node) as client:
            remote = '/tmp/buggcraft-native-release-' + TAG + '.tar.gz'
            with client.open_sftp() as sftp:
                sftp.put(str(bundle), remote)
            output = remote_python(client, f'''
                import hashlib, json, os, pathlib, subprocess, tarfile
                release=pathlib.Path({RELEASE!r}); archive=pathlib.Path({remote!r})
                assert hashlib.sha256(archive.read_bytes()).hexdigest()=={digest!r}
                assert not release.exists(), 'Release already exists; inspect instead of overwriting'
                release.mkdir(mode=0o755)
                with tarfile.open(archive) as source:
                    for item in source:
                        target=(release/item.name).resolve()
                        assert target.is_relative_to(release), 'Unsafe archive member'
                        if item.issym() or item.islnk():
                            assert not pathlib.Path(item.linkname).is_absolute()
                    source.extractall(release, filter='data')
                subprocess.run(['python3','-m','venv',str(release/'.venv')],check=True)
                result=subprocess.run([str(release/'.venv/bin/pip'),'install','--no-index',
                    '--find-links=/opt/buggcraft-wheelhouse','-r',str(release/'requirements.txt')],
                    text=True,capture_output=True)
                if result.returncode: raise RuntimeError(result.stderr)
                subprocess.run([str(release/'.venv/bin/python'),'-m','compileall','-q',str(release/'src')],check=True)
                subprocess.run([str(release/'.venv/bin/pip'),'check'],check=True,capture_output=True)
                binary=release/'bin/buggcraft-core'
                linked=subprocess.check_output(['ldd',str(binary)],text=True)
                assert 'not found' not in linked
                version=subprocess.run(['runuser','-u','buggcraft-node','--',str(binary),'--version'],
                    capture_output=True,text=True,check=True).stdout.strip()
                print(json.dumps(dict(release=str(release),version=version,
                    core_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),ldd=linked,
                    service_untouched=True)))
            ''')
            result = json.loads(output)
            save(node + '-staged.json', result)
            print(node + ' staged: ' + result['version'], flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(stage, ('node1', 'node2')))
    validation = '/tmp/buggcraft-native-release-validation-' + TAG
    modules = ('test_relay_security test_capacity test_peer_wire test_peer_signaling_stun '
               'test_heartbeat test_account_replication test_federation_integrity test_player_diagnostics '
               'test_web_accounts test_feedback test_portal_governance test_mail test_bandwidth test_onboarding '
               'test_node_startup')
    with connect('node1') as client:
        with client.open_sftp() as sftp:
            sftp.put(str(ARTIFACT / 'validation-source.tar.gz'), validation + '.tar.gz')
        command(client, 'mkdir -p ' + validation + ' && tar xzf ' + validation + '.tar.gz -C ' + validation)
        run = (f'cd {validation} && ln -s ../web compatibility/src && '
               f'BUGGCRAFT_NATIVE_CORE={RELEASE}/bin/buggcraft-core '
               f'{RELEASE}/.venv/bin/python scripts/check_compatibility.py {modules} '
               '> regression.log 2>&1; echo $? > regression.status')
        # The non-root validation account can read the release but writes only to /tmp.
        command(client, 'nohup sh -c ' + shlex.quote(run) + ' </dev/null >/dev/null 2>&1 &')
    save('validation.json', dict(directory=validation, modules=modules.split()))
    print('Isolated regression suite started: ' + validation, flush=True)


def activate(node):
    # Keep rollout sequential. Never update both active entry points at once.
    with connect('node1') as client:
        validation='/tmp/buggcraft-native-release-validation-' + TAG
        status=command(client,'cat ' + validation + '/regression.status').strip()
        with client.open_sftp() as sftp:
            sftp.get(validation+'/regression.log',str(ARTIFACT/'predeployment-regression.log'))
        if status!='0':
            raise RuntimeError('The release regression suite has not passed; production remains unchanged')
    with connect(node) as client:
        output = remote_python(client, f'''
            import configparser, hashlib, json, os, pathlib, shutil, ssl, subprocess, tarfile, time, urllib.request
            data=pathlib.Path('/var/lib/buggcraft-node')
            release=pathlib.Path({RELEASE!r}); current=pathlib.Path('/opt/buggcraft-node/current')
            assert release.is_dir() and (release/'bin/buggcraft-core').is_file()
            old=current.resolve(); assert old!=release, 'Release already active'
            config=configparser.ConfigParser(interpolation=None); config.read(data/'server.conf')
            url='https://127.0.0.1:'+config['web'].get('port','18443')+'/api/status'
            def health():
                return json.loads(urllib.request.urlopen(url,context=ssl._create_unverified_context(),timeout=4).read())
            initial=health()
            assert initial.get('players',0)==0 and initial.get('rooms',0)==0, 'Players or rooms active; defer rollout'
            backup=pathlib.Path('/var/backups/buggcraft-native-'+{TAG!r})
            backup.mkdir(mode=0o700,exist_ok=False)
            unit=pathlib.Path('/etc/systemd/system/buggcraft-node.service')
            shutil.copy2(unit,backup/'buggcraft-node.service')
            (backup/'previous-release.txt').write_text(str(old)+'\\n')
            protected=[data/'server.conf',data/'relay-profile.json']
            for section in config.sections():
                for key,value in config[section].items():
                    if key.endswith('_file') or key in ('host_key','tls_cert','tls_key','cert','key'):
                        candidate=pathlib.Path(value)
                        if not candidate.is_absolute(): candidate=data/candidate
                        if candidate.is_file() and candidate not in protected: protected.append(candidate)
            before={{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}}
            def service(action): subprocess.run(['systemctl',action,'buggcraft-node'],check=True,timeout=35)
            service('stop')
            try:
                with tarfile.open(backup/'persistent-data.tar.gz','w:gz') as archive:
                    archive.add(data,arcname='buggcraft-node')
                os.chmod(backup/'persistent-data.tar.gz',0o600)
                next_link=current.with_name('current.native-next')
                assert not next_link.exists() and not next_link.is_symlink()
                next_link.symlink_to(release,target_is_directory=True); os.replace(next_link,current)
                service('start')
                status=None
                for attempt in range(30):
                    try:
                        status=health()
                        if status.get('status')=='online': break
                    except Exception: pass
                    time.sleep(1)
                assert status and status.get('status')=='online', 'Local status failed'
                pid=int(subprocess.check_output(['systemctl','show','buggcraft-node','--property=MainPID','--value'],text=True))
                children=pathlib.Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children').read_text().split()
                cores=[child for child in children if pathlib.Path('/proc/'+child+'/exe').resolve()==release/'bin/buggcraft-core']
                assert len(cores)==1, 'Native worker child missing'
                after={{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}}
                assert before==after, 'Identity or configuration unexpectedly changed'
                assert (data/'logs/players').is_dir(), 'Player journal directory missing'
                result=dict(release=str(release),previous_release=str(old),backup=str(backup),
                    status={{k:status.get(k) for k in ('status','players','rooms','network_nodes','streams')}},
                    main_pid=pid,native_pid=int(cores[0]),protected_files_unchanged=True,
                    protected_file_count=len(before),service_active=True,
                    unit_unchanged=unit.read_bytes()==(backup/'buggcraft-node.service').read_bytes())
                (backup/'deployment-result.json').write_text(json.dumps(result,indent=2)+'\\n')
                print(json.dumps(result))
            except BaseException:
                subprocess.run(['systemctl','stop','buggcraft-node'],timeout=35)
                restore=current.with_name('current.native-rollback')
                restore.symlink_to(old,target_is_directory=True); os.replace(restore,current)
                service('start')
                print(json.dumps(dict(rolled_back=True,release=str(old),backup=str(backup))))
                raise
        ''')
        result = json.loads(output)
        save(node + '-activated.json', result)
        print(json.dumps(dict(node=node, **result)), flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','activate'])
    parser.add_argument('--node',choices=['node1','node2'])
    args=parser.parse_args()
    if args.phase=='prepare': prepare()
    else:
        if not args.node: parser.error('--node required for sequential activation')
        activate(args.node)
