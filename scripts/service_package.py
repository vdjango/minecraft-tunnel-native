"""Package current service roles without obsolete node/management web assets."""
import argparse
from pathlib import Path
import re
import shutil


def identity_assets(directory, entry='account.html'):
    """Follow one built page's local asset graph, excluding other pages."""
    directory = Path(directory).resolve()
    pending, found = [entry], set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError('Missing or invalid identity asset: ' + name)
        found.add(name)
        if path.suffix not in ('.html', '.js', '.css'):
            continue
        text = path.read_text(encoding='utf-8')
        for prefix,target in re.findall(r'''(?:["']|url\(["']?)(/|\./)([^"'?#\s)]+\.(?:js|css|svg|png|webp|woff2?))(?:[?][^"')\s]*)?''', text):
            candidate = (directory if prefix=='/' else path.parent) / target
            if candidate.is_file():
                pending.append(candidate.relative_to(directory).as_posix())
    return sorted(found)


def copy_service(source, destination, *, role):
    """Copy code once; relays ship no HTML/JS/CSS and identity ships only its page."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for path in source.rglob('*.py'):
        if '__pycache__' in path.parts or path.is_relative_to(source / 'tunnel/web'):
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if role == 'identity':
        assets = source / 'tunnel/web'
        for name in identity_assets(assets):
            target = destination / 'tunnel/web' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(assets / name, target)
    elif role != 'relay':
        raise ValueError('Unknown service role')


def copy_node_payload(destination):
    """Shared source/licence/defaults layout for deb and frozen node packages."""
    root, destination = Path(__file__).resolve().parents[1], Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    copy_service(root / 'web', destination / 'web', role='relay')
    shutil.copytree(root / 'licenses', destination / 'licenses')
    (destination / 'scripts').mkdir()
    for name in ('configure_node.py', 'node_cli.py'):
        shutil.copy2(root / 'scripts' / name, destination / 'scripts' / name)
    (destination / 'packaging').mkdir()
    shutil.copy2(root / 'packaging/node-defaults.json', destination / 'packaging/node-defaults.json')
    for name in ('PROTOCOL_STANDARD.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE'):
        shutil.copy2(root / name, destination / name)


def copy_node_runtime(runtime, destination):
    """Keep the transport with its private libraries; exclude bridge/SDK development files."""
    runtime, destination = Path(runtime), Path(destination)
    (destination / 'bin').mkdir(parents=True)
    for path in (runtime / 'bin').iterdir():
        if path.is_file() and (path.name in ('buggcraft-core', 'buggcraft-core.exe') or
                             path.suffix.lower() == '.dll' and 'buggcraft_bridge' not in path.name):
            shutil.copy2(path, destination / 'bin' / path.name)
    if (runtime / 'lib').is_dir():
        shutil.copytree(runtime / 'lib', destination / 'lib', symlinks=True,
                        ignore=shutil.ignore_patterns('*buggcraft_bridge*', 'cmake', 'pkgconfig', '*.a'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('identity', 'relay'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    copy_service(Path(__file__).resolve().parents[1] / 'web', args.output, role=args.role)


if __name__ == '__main__':
    main()
