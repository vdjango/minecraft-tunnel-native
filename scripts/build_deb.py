"""Build a self-contained Ubuntu 24.04 package from a CMake install directory.

Run under the system Python 3.12 (a venv is fine). The build downloads pinned
wheels; installation itself never runs pip or downloads application code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def build(runtime, output, version):
    if platform.system() != 'Linux' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Build on Ubuntu 24.04 with Python 3.12; cross packaging is not supported.')
    release = platform.freedesktop_os_release()
    if (release.get('ID'), release.get('VERSION_ID')) != ('ubuntu', '24.04'):
        raise SystemExit('This package recipe currently targets Ubuntu 24.04.')
    arch = run('dpkg', '--print-architecture')
    runtime = runtime.resolve()
    if not (runtime / 'bin/buggcraft-core').is_file():
        raise SystemExit('First run cmake --install build --prefix runtime')
    run('dpkg', '--validate-version', version)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='buggcraft-deb-') as temporary:
        work = Path(temporary)
        stage = work / 'package'
        app = stage / 'usr/lib/buggcraft-node'
        app.mkdir(parents=True)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.log')
        for folder in ('web', 'site', 'licenses'):
            shutil.copytree(ROOT / folder, app / folder, ignore=ignore)
        for name in ('PROTOCOL_STANDARD.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE'):
            shutil.copy2(ROOT / name, app / name)
        native = app / 'native'
        (native / 'bin').mkdir(parents=True)
        shutil.copy2(runtime / 'bin/buggcraft-core', native / 'bin/buggcraft-core')
        # Keep SONAME links and the private libssh, exclude the client bridge.
        shutil.copytree(runtime / 'lib', native / 'lib', symlinks=True,
                        ignore=shutil.ignore_patterns('*buggcraft_bridge*', 'cmake', 'pkgconfig', '*.a'))
        python = app / 'python'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-compile',
                        '--target', str(python), '-r', str(ROOT / 'requirements.txt')], check=True)
        for name, dest in [('buggcraft-node', stage / 'usr/bin/buggcraft-node'),
                           ('buggcraft-node.service', stage / 'usr/lib/systemd/system/buggcraft-node.service')]:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'packaging/debian' / name, dest)
            dest.chmod(0o755 if name == 'buggcraft-node' else 0o644)
        debian = stage / 'DEBIAN'
        debian.mkdir()
        for name in ('postinst', 'prerm', 'postrm'):
            shutil.copy2(ROOT / 'packaging/debian' / name, debian / name)
            (debian / name).chmod(0o755)
        # Derive symbol-version dependencies from the actual ELF files,
        # including native Python wheels and the bundled SSH implementation.
        (work / 'debian').mkdir()
        (work / 'debian/control').write_text('Source: buggcraft-node\nSection: net\nPriority: optional\n'
            'Maintainer: Buggcraft contributors <noreply@github.com>\n\nPackage: buggcraft-node\n'
            'Architecture: any\nDescription: Native Minecraft community node\n')
        elfs = []
        for path in app.rglob('*'):
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as source:
                    if source.read(4) == b'\x7fELF':
                        elfs.append(path)
        deps = run('dpkg-shlibdeps', '--ignore-missing-info', '-O', '-l' + str(native / 'lib'),
                   *['-e' + str(p) for p in elfs], cwd=work)
        deps = next(line.split('=', 1)[1] for line in deps.splitlines() if line.startswith('shlibs:Depends='))
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024
        (debian / 'control').write_text(f'Package: buggcraft-node\nVersion: {version}\nArchitecture: {arch}\n'
            'Maintainer: Buggcraft contributors <noreply@github.com>\nSection: net\nPriority: optional\n'
            f'Installed-Size: {size}\nDepends: python3 (>= 3.12~), python3 (<< 3.13), adduser, ca-certificates, init-system-helpers, {deps}\n'
            'Homepage: https://github.com/vdjango/minecraft-tunnel-native\n'
            'Description: Self-hosted Minecraft rooms with native P2P and encrypted relay\n'
            ' C++20 transport and a browser-based node administration interface.\n')
        manifest = {str(p.relative_to(app)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in app.rglob('*') if p.is_file()}
        (app / 'manifest.json').write_text(json.dumps(dict(version=version, architecture=arch, files=manifest), indent=2))
        target = output.resolve() / f'buggcraft-node_{version}_{arch}.deb'
        subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(stage), str(target)], check=True)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix('.deb.sha256').write_text(f'{checksum}  {target.name}\n')
        print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/debian')
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    build(args.runtime, args.output, args.version)
