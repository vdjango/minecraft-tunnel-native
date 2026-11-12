"""Package the headless node on its target OS; Windows preserves official Python binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from service_package import copy_node_payload, copy_node_runtime

ROOT = Path(__file__).resolve().parents[1]


def windows_runtime(bundle, work):
    """Vendor the hash-pinned official embed distribution and prebuilt dependencies."""
    lock = json.loads((ROOT / 'packaging/python-runtime.lock.json').read_text(encoding='utf-8'))
    record = lock['windows-x64']
    archive = ROOT / '.deps' / ('python-' + lock['version'] + '-embed-amd64.zip')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        data = archive.read_bytes()
    else:
        data = b''
        for url in (record['url'], record['mirror']):
            try:
                with urllib.request.urlopen(url, timeout=20) as source:
                    data = source.read(20000001)
                if hashlib.sha256(data).hexdigest() == record['sha256']:
                    break
            except OSError:
                continue
    if len(data) > 20000000 or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('Official Python runtime SHA-256 mismatch; refusing to package')
    if not archive.exists():
        archive.write_bytes(data)
    runtime = bundle / 'runtime'
    runtime.mkdir()
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if not (runtime / name).resolve().is_relative_to(runtime.resolve()):
                raise ValueError('Invalid Python runtime archive path')
        source.extractall(runtime)
    minor = ''.join(lock['version'].split('.')[:2])
    (runtime / ('python' + minor + '._pth')).write_text(
        'python' + minor + '.zip\n.\n../python\n../web\n../scripts\nimport site\n', encoding='ascii')
    # Preserve original PSF signatures. Hash validation alone does not establish publisher identity.
    paths = [runtime / 'python.exe', runtime / ('python' + minor + '.dll')]
    literal_paths = ','.join("'" + str(path).replace("'", "''") + "'" for path in paths)
    check = ("$ErrorActionPreference='Stop'; foreach ($p in @(" + literal_paths + ")) { "
             "$s=Get-AuthenticodeSignature -LiteralPath $p; if ($s.Status -ne 'Valid' -or "
             "$s.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Invalid Python publisher signature' } }")
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', check], check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-compile',
                    '--platform', 'win_amd64', '--implementation', 'cp', '--python-version',
                    '.'.join(lock['version'].split('.')[:2]), '--abi', 'cp' + minor,
                    '--target', str(bundle / 'python'), '-r', str(ROOT / 'requirements.txt')], check=True)
    command = bundle / 'Start Node.cmd'
    command.write_text('@echo off\r\n"%~dp0runtime\\python.exe" "%~dp0scripts\\node_cli.py" %*\r\n', encoding='ascii')
    return [str(runtime / 'python.exe'), str(bundle / 'scripts/node_cli.py')]


def build(runtime, output, version, installer=False):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?', version):
        raise ValueError('Invalid package version')
    suffix = '.exe' if sys.platform == 'win32' else ''
    runtime, output = runtime.resolve(), output.resolve()
    if not (runtime / ('bin/buggcraft-core' + suffix)).is_file():
        raise ValueError('CMake install runtime is missing the native core')
    architecture = {'AMD64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if not architecture:
        raise ValueError('Unsupported package architecture')
    target_os = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='buggcraft-node-package-') as temporary:
        work = Path(temporary)
        payload = work / 'buggcraft-node'
        copy_node_payload(payload)
        copy_node_runtime(runtime, payload / 'native')
        if target_os == 'windows':
            bundle = payload
            command = windows_runtime(bundle, work)
        else:
            subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--noupx',
                            '--name', 'buggcraft-node', '--distpath', str(work / 'dist'), '--workpath', str(work / 'build'),
                            '--specpath', str(work), '--paths', str(ROOT / 'web'), '--collect-submodules', 'tunnel',
                            '--collect-all', 'asyncssh', '--collect-all', 'aiohttp', '--hidden-import', 'psutil',
                            '--add-data', str(payload) + ':.', str(ROOT / 'scripts/node_cli.py')], check=True)
            bundle = work / 'dist/buggcraft-node'
            command = [str(bundle / ('buggcraft-node' + suffix))]
        # A successful CLI does not substitute for the installed-node protocol test.
        subprocess.run([*command, 'join', '--help'], check=True)
        if target_os == 'macos':
            start = bundle / 'Start Node.command'
            start.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")"\nexec ./buggcraft-node\n', encoding='utf-8')
            start.chmod(0o755)
        archive_base = output / f'buggcraft-node-{version}-{target_os}-{architecture}'
        archive = Path(shutil.make_archive(str(archive_base), 'zip' if target_os == 'windows' else 'gztar',
            root_dir=bundle.parent, base_dir=bundle.name))
        assets = [archive]
        if installer and target_os == 'windows':
            compiler = shutil.which('ISCC') or 'C:/Program Files (x86)/Inno Setup 6/ISCC.exe'
            subprocess.run([compiler, '/DBundle=' + str(bundle), '/DVersion=' + version,
                            '/DOutput=' + str(output), str(ROOT / 'packaging/windows/node.iss')], check=True)
            assets.append(output / f'buggcraft-node-{version}-windows-x64-setup.exe')
        elif installer and target_os == 'macos':
            package = output / f'buggcraft-node-{version}-macos-{architecture}.pkg'
            subprocess.run(['pkgbuild', '--root', str(bundle), '--identifier', 'icu.bugg.node',
                            '--version', version.split('-')[0], '--install-location', '/Applications/BuggcraftNode',
                            str(package)], check=True)
            assets.append(package)
        for file in assets:
            file.with_name(file.name + '.sha256').write_text(
                hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name + '\n', encoding='ascii')
        (output / f'node-build-{target_os}-{architecture}.json').write_text(json.dumps(dict(
            version=version, system=platform.platform(), python=platform.python_version(),
            architecture=architecture, assets=[file.name for file in assets]), indent=2), encoding='utf-8')
        print('\n'.join(str(file) for file in assets))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / 'package')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/release-assets')
    parser.add_argument('--version', required=True)
    parser.add_argument('--installer', action='store_true')
    args = parser.parse_args()
    build(args.runtime, args.output, args.version, args.installer)
