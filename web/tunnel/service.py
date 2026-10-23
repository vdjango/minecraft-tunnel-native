"""Install the main entry as a native systemd service; standard library only."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SERVICE = 'buggcraft-node'
MARKER = '# Managed by Buggcraft main --install-service\n'


def unit_file(root, web_port, *, packaged=False):
    entry = (f'{root}/current/buggcraft-node' if packaged else
             f'{root}/current/.venv/bin/python -u {root}/current/src/main.py')
    return MARKER + f'''[Unit]
Description=Buggcraft federation node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={SERVICE}
Group={SERVICE}
WorkingDirectory={root}/current
ExecStart={entry} --directory /var/lib/{SERVICE} --web-port {web_port}
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
StateDirectory={SERVICE}
StateDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
UMask=0077
LimitNOFILE=8192
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def command(args, **kwargs):
    try:
        return subprocess.run([str(arg) for arg in args], check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f'Command failed ({error.returncode}): {args[0]}') from error


def prepare_release(source, root):
    """Build separately from the running release and copy no credentials or tests."""
    releases = root / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-'), dir=releases))
    release.chmod(0o755)
    shutil.copytree(source / 'src', release / 'src',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('requirements.txt', 'PROTOCOL_STANDARD.md'):
        shutil.copyfile(source / name, release / name)
    try:
        command([sys.executable, '-m', 'venv', release / '.venv'])
    except RuntimeError:
        raise RuntimeError('Cannot create Python environment. Install the venv package matching your Python version '
                           '(Ubuntu/Debian: sudo apt install python3-venv), then retry.') from None
    command([release / '.venv/bin/python', '-m', 'pip', 'install', '--disable-pip-version-check',
             '-r', release / 'requirements.txt'])
    command([release / '.venv/bin/python', '-c', 'import asyncssh, aiohttp, cryptography, psutil'])
    return release


def prepare_packaged_release(source, root):
    """Copy the self-contained runtime, excluding all mutable node data."""
    if not (source / 'buggcraft-node').is_file() or not (source / '_internal').is_dir():
        raise RuntimeError('Incomplete portable package: keep the executable and _internal together.')
    releases = root / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-'), dir=releases))
    release.chmod(0o755)
    shutil.copy2(source / 'buggcraft-node', release / 'buggcraft-node')
    shutil.copytree(source / '_internal', release / '_internal')
    command([release / 'buggcraft-node', '--help'], stdout=subprocess.DEVNULL)
    return release


def ensure_account():
    import pwd
    try:
        account = pwd.getpwnam(SERVICE)
    except KeyError:
        shell = shutil.which('nologin')
        if not shell:
            raise RuntimeError('The nologin command is required to create the service account.')
        command(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/' + SERVICE,
                 '--no-create-home', '--shell', shell, SERVICE])
        return
    if account.pw_uid == 0 or Path(account.pw_shell).name not in ('nologin', 'false'):
        raise RuntimeError('Existing buggcraft-node user is not a dedicated service account; installation stopped.')
    import grp
    if grp.getgrgid(account.pw_gid).gr_name != SERVICE:
        raise RuntimeError('Existing buggcraft-node user must have its own buggcraft-node group.')


def activate(root, release, unit, web_port, *, packaged=False):
    """Switch only after dependencies are ready; restore the former unit on failure."""
    current = root / 'current'
    if current.exists() and not current.is_symlink():
        raise RuntimeError(f'Refusing to replace an unmanaged directory: {current}')
    previous = os.readlink(current) if current.is_symlink() else None
    old_unit = unit.read_bytes() if unit.exists() else None
    if old_unit is not None and not old_unit.startswith(MARKER.encode()):
        raise RuntimeError(f'Refusing to replace an unmanaged service: {unit}')
    was_enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', SERVICE], check=False).returncode == 0
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE], check=False).returncode == 0
    if was_active:
        command(['systemctl', 'stop', SERVICE])
    temporary = root / 'current.next'
    try:
        temporary.symlink_to(release, target_is_directory=True)
        temporary.replace(current)
        staged_unit = unit.with_name(unit.name + '.new')
        staged_unit.write_text(unit_file(root, web_port, packaged=packaged), encoding='utf-8')
        staged_unit.replace(unit)
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'enable', SERVICE])
        command(['systemctl', 'start', SERVICE])
        command(['systemctl', 'is-active', '--quiet', SERVICE])
    except BaseException:
        subprocess.run(['systemctl', 'stop', SERVICE], check=False)
        if not was_enabled:
            subprocess.run(['systemctl', 'disable', SERVICE], check=False)
        temporary.unlink(missing_ok=True)
        unit.with_name(unit.name + '.new').unlink(missing_ok=True)
        if previous is not None:
            temporary.symlink_to(previous, target_is_directory=True)
            temporary.replace(current)
        else:
            current.unlink(missing_ok=True)
        if old_unit is None:
            unit.unlink(missing_ok=True)
        else:
            unit.write_bytes(old_unit)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        if was_active:
            subprocess.run(['systemctl', 'start', SERVICE], check=False)
        raise


def install(*, web_port=18443):
    if not 1024 <= web_port <= 65535:
        raise RuntimeError('The service HTTPS port must be between 1024 and 65535.')
    if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('Service installation requires Linux and sudo/root.')
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required.')
    if not Path('/run/systemd/system').is_dir() or not shutil.which('systemctl'):
        raise RuntimeError('This host must be running systemd.')
    # A second installer must not stop or overwrite the first installer’s service.
    import fcntl
    with open('/run/buggcraft-node-install.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another node installation is in progress.') from None
        packaged = bool(getattr(sys, 'frozen', False))
        source = Path(sys.executable).resolve().parent if packaged else Path(__file__).resolve().parents[2]
        root = Path('/opt/buggcraft-node')
        unit = Path('/etc/systemd/system/buggcraft-node.service')
        if unit.exists() and not unit.read_bytes().startswith(MARKER.encode()):
            raise RuntimeError(f'Existing unmanaged unit must be migrated manually: {unit}')
        ensure_account()
        old_mask = os.umask(0o022)
        try:
            release = prepare_packaged_release(source, root) if packaged else prepare_release(source, root)
        finally:
            os.umask(old_mask)
        activate(root, release, unit, web_port, packaged=packaged)
    print('Service installed. Data: /var/lib/buggcraft-node', flush=True)
    print('Open the private First setup HTTPS link below; replace SERVER_IP with the server address.', flush=True)
    subprocess.run(['journalctl', '-u', SERVICE, '-n', '15', '--no-pager', '-o', 'cat'], check=False)
    print('If the link is not visible yet: sudo journalctl -u buggcraft-node -n 30 --no-pager', flush=True)
