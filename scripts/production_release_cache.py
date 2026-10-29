"""Deploy the static download site and install its hourly GitHub release mirror."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import posixpath
import shlex
import stat
import textwrap

from production_remote import connect


ROOT = Path(__file__).resolve().parents[1]


def remote_python(client, script):
    command = 'sudo -n python3 -c ' + shlex.quote(textwrap.dedent(script))
    _, stdout, stderr = client.exec_command(command, timeout=1200)
    output = stdout.read().decode(errors='replace')
    error = stderr.read().decode(errors='replace')
    status = stdout.channel.recv_exit_status()
    if status:
        raise RuntimeError(f'Remote deployment failed ({status}): {error[-3000:]} {output[-3000:]}')
    return output


def deploy():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    stage = f'/tmp/buggcraft-release-cache-deploy-{stamp}'
    site_files = sorted(path for path in (ROOT / 'site').rglob('*') if path.is_file())
    unit_files = [
        ROOT / 'scripts' / 'sync_release_cache.py',
        ROOT / 'packaging' / 'systemd' / 'buggcraft-release-cache.service',
        ROOT / 'packaging' / 'systemd' / 'buggcraft-release-cache.timer',
    ]
    local_files = []
    for path in site_files:
        if path.is_symlink():
            raise RuntimeError(f'Refusing to publish symbolic link: {path}')
        relative = path.relative_to(ROOT / 'site').as_posix()
        local_files.append((path, f'site/{relative}', f'/opt/buggcraft-site/{relative}', 0o644))
    for path, destination in zip(unit_files, (
        '/usr/local/libexec/buggcraft-release-cache.py',
        '/etc/systemd/system/buggcraft-release-cache.service',
        '/etc/systemd/system/buggcraft-release-cache.timer',
    )):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f'Missing release cache deployment file: {path}')
        local_files.append((path, f'install/{path.name}', destination, 0o755 if path.name.endswith('.py') else 0o644))
    snippet = ROOT / 'packaging' / 'nginx' / 'buggcraft-release-cache.locations.conf'
    local_files.append((snippet, f'install/{snippet.name}',
                        '/etc/nginx/snippets/buggcraft-release-cache.locations.conf', 0o644))

    entries = []
    for path, staged, destination, mode in local_files:
        entries.append(dict(
            staged=staged,
            destination=destination,
            mode=mode,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ))

    with connect('node1') as client:
        with client.open_sftp() as sftp:
            sftp.mkdir(stage, mode=0o700)
            for path, staged, _, _ in local_files:
                remote_path = posixpath.join(stage, staged)
                parent = posixpath.dirname(remote_path)
                current = ''
                for component in parent.strip('/').split('/'):
                    current += '/' + component
                    try:
                        sftp.mkdir(current, mode=0o700)
                    except IOError:
                        attributes = sftp.stat(current)
                        if not stat.S_ISDIR(attributes.st_mode):
                            raise NotADirectoryError(current)
                sftp.put(str(path), remote_path)

        output = remote_python(client, f'''
            import hashlib, json, os, pathlib, shutil, subprocess, tempfile
            stage=pathlib.Path({stage!r})
            root=pathlib.Path('/opt/buggcraft-site')
            backup=pathlib.Path('/var/backups/buggcraft-release-cache-{stamp}')
            rows=json.loads({json.dumps(entries)!r})
            assert stage.parent==pathlib.Path('/tmp') and stage.name.startswith('buggcraft-release-cache-deploy-')
            assert root.is_dir() and (root/'download').is_dir()
            assert not backup.exists(), 'Deployment backup already exists'
            backup.mkdir(mode=0o700,parents=True)
            touched=[]
            timer_was_active=subprocess.run(['systemctl','is-active','--quiet',
                'buggcraft-release-cache.timer']).returncode==0
            timer_was_enabled=subprocess.run(['systemctl','is-enabled','--quiet',
                'buggcraft-release-cache.timer']).returncode==0
            try:
                for row in rows:
                    source=stage/row['staged']
                    destination=pathlib.Path(row['destination'])
                    assert source.is_file() and not source.is_symlink()
                    assert hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256']
                    if destination.is_relative_to(root):
                        assert destination.resolve().is_relative_to(root.resolve())
                    else:
                        assert str(destination) in (
                            '/usr/local/libexec/buggcraft-release-cache.py',
                            '/etc/systemd/system/buggcraft-release-cache.service',
                            '/etc/systemd/system/buggcraft-release-cache.timer',
                            '/etc/nginx/snippets/buggcraft-release-cache.locations.conf')
                    old=backup/str(destination).lstrip('/')
                    previous=destination.read_bytes() if destination.exists() else None
                    if previous is not None:
                        old.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(destination,old)
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    temporary=destination.with_name(destination.name+'.deploy-{stamp}')
                    assert not temporary.exists()
                    shutil.copyfile(source,temporary)
                    temporary.chmod(row['mode'])
                    os.replace(temporary,destination)
                    touched.append((destination,old,previous is not None))
                nginx_link=pathlib.Path('/etc/nginx/sites-enabled/buggcraft-domains')
                nginx_config=nginx_link.resolve(strict=True)
                assert nginx_config==pathlib.Path('/etc/nginx/sites-available/buggcraft-domains')
                include_line='    include /etc/nginx/snippets/buggcraft-release-cache.locations.conf;\\n'
                anchor='    # The download service owns release files and validates the exact bytes.\\n'
                config_text=nginx_config.read_text(encoding='utf-8')
                if include_line not in config_text:
                    assert config_text.count(anchor)==1, 'Expected a single download-server anchor in Nginx'
                    config_bytes=config_text.replace(anchor,include_line+anchor).encode('utf-8')
                    old=backup/str(nginx_config).lstrip('/')
                    old.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(nginx_config,old)
                    temporary=nginx_config.with_name(nginx_config.name+'.deploy-{stamp}')
                    assert not temporary.exists()
                    temporary.write_bytes(config_bytes)
                    temporary.chmod(nginx_config.stat().st_mode & 0o777)
                    os.replace(temporary,nginx_config)
                    touched.append((nginx_config,old,True))
                subprocess.run(['nginx','-t'],check=True,capture_output=True)
                subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True)
                subprocess.run(['systemctl','start','buggcraft-release-cache.service'],check=True,
                               capture_output=True,timeout=1200)
                cache=json.loads((root/'download/releases.json').read_text(encoding='utf-8'))
                assert cache['releases'] and cache.get('updated_at')
                for release in cache['releases']:
                    for asset in release.get('assets',[]):
                        url=asset['browser_download_url']
                        assert url.startswith('/release-cache/')
                        local=root/'download'/url.lstrip('/')
                        assert local.is_file() and local.stat().st_size==asset['size']
                subprocess.run(['systemctl','enable','--now','buggcraft-release-cache.timer'],
                               check=True,capture_output=True)
                subprocess.run(['systemctl','reload','nginx'],check=True,capture_output=True)
                timer=subprocess.check_output(['systemctl','show','buggcraft-release-cache.timer',
                    '-p','ActiveState','-p','NextElapseUSecRealtime'],text=True).strip()
                subprocess.run(['nginx','-t'],check=True,capture_output=True)
                result=dict(files=len(rows),backup=str(backup),release_count=len(cache['releases']),
                    asset_count=sum(len(r.get('assets',[])) for r in cache['releases']),
                    cache_updated_at=cache['updated_at'],timer=timer,
                    nginx_configuration_valid=True)
                print(json.dumps(result))
            except BaseException:
                subprocess.run(['systemctl','stop','buggcraft-release-cache.timer'],capture_output=True)
                for destination,old,had_previous in reversed(touched):
                    if had_previous:
                        temporary=destination.with_name(destination.name+'.rollback-{stamp}')
                        shutil.copy2(old,temporary)
                        os.replace(temporary,destination)
                    else:
                        destination.unlink(missing_ok=True)
                subprocess.run(['systemctl','daemon-reload'],capture_output=True)
                if subprocess.run(['nginx','-t'],capture_output=True).returncode==0:
                    subprocess.run(['systemctl','reload','nginx'],capture_output=True)
                if timer_was_enabled:
                    subprocess.run(['systemctl','enable','buggcraft-release-cache.timer'],capture_output=True)
                else:
                    subprocess.run(['systemctl','disable','buggcraft-release-cache.timer'],capture_output=True)
                if timer_was_active:
                    subprocess.run(['systemctl','start','buggcraft-release-cache.timer'],capture_output=True)
                raise
            finally:
                if stage.parent==pathlib.Path('/tmp') and stage.name.startswith('buggcraft-release-cache-deploy-'):
                    shutil.rmtree(stage)
        ''')
    result = json.loads(output)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    deploy()
