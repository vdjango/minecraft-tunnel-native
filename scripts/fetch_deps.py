"""Fetch pinned upstream sources used by the CMake build (no executable installers)."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'cmake' / 'native-dependencies.json').read_text(encoding='utf-8'))
SOURCES = {
    'libssh': ('https://www.libssh.org/files/0.12/libssh-0.12.2.tar.xz', 'libssh-0.12.2',
               '49560f677d96e3706a904ac2de1116e25f3680937d51e5c92198fcba4a1c1e9f'),
    'json': ('https://raw.githubusercontent.com/nlohmann/json/v3.12.0/single_include/nlohmann/json.hpp',
             'json/single_include/nlohmann/json.hpp',
             'aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63'),
    'zlib': ('https://zlib.net/zlib-1.3.2.tar.gz', 'zlib-1.3.2',
             'bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16'),
}
LIBSSH_MIRROR_COMMIT = '07430deb9b97b751ec5ea5a7fc307f40bf042e0a'

def fetch(item):
    name, (url, folder, expected) = item
    base = ROOT / '.deps'
    base.mkdir(exist_ok=True)
    archive = base / ('libssh.tar.xz' if name == 'libssh' else ('zlib.tar.gz' if name == 'zlib' else folder))
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'Buggcraft-native-build'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read()
        except OSError:
            if name == 'libssh':
                target = base / folder
                if not target.exists():
                    temporary = base / (folder + '.git-tmp')
                    if temporary.exists():
                        import shutil
                        shutil.rmtree(temporary)
                    subprocess.run([
                        'git', 'clone', '--depth', '1', '--branch', 'libssh-0.12.2',
                        'https://github.com/libssh/libssh-mirror.git', str(temporary),
                    ], check=True)
                    revision = git('rev-parse', 'HEAD', cwd=temporary)
                    if revision != LIBSSH_MIRROR_COMMIT:
                        raise ValueError(f'libssh mirror revision mismatch: {revision}')
                    temporary.replace(target)
                return f'libssh commit={LIBSSH_MIRROR_COMMIT} source={target}'
            if name not in ('json', 'zlib'):
                raise
            mirror = ('https://cdn.jsdelivr.net/gh/nlohmann/json@v3.12.0/single_include/nlohmann/json.hpp'
                      if name == 'json' else 'https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz')
            with urllib.request.urlopen(mirror, timeout=45) as response:
                content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('Dependency SHA-256 mismatch: ' + name)
        temporary = archive.with_suffix(archive.suffix + '.tmp')
        temporary.write_bytes(content)
        temporary.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError('Dependency SHA-256 mismatch: ' + name)
    target = base / folder
    if name in ('libssh', 'zlib') and not target.exists():
        with tarfile.open(archive) as source:
            source.extractall(base, filter='data')
    return f'{name} sha256={digest} source={target}'


def git(*arguments, cwd=None):
    return subprocess.run(['git', *arguments], cwd=cwd, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True).stdout.strip()


def fetch_git(name):
    entry = LOCK[name]
    target = ROOT / '.deps' / name
    if not target.exists():
        tag = {'libdatachannel': 'v0.24.6', 'zstd': 'v1.5.7'}[name]
        git('clone', '--depth', '1', '--branch', tag,
            '--recurse-submodules', '--shallow-submodules', '--jobs', '4',
            entry['repository'], str(target))
    if not (target / '.git').exists():
        raise ValueError(f'{target} exists but is not a pinned Git checkout')
    commit = git('rev-parse', 'HEAD', cwd=target)
    if commit != entry['commit']:
        raise ValueError(f'{name} revision mismatch: {commit}')
    for path, expected in entry.get('submodules', {}).items():
        submodule = target / path
        if not (submodule / '.git').exists():
            git('submodule', 'update', '--init', '--depth', '1', '--', path, cwd=target)
        actual = git('rev-parse', 'HEAD', cwd=submodule)
        if actual != expected:
            raise ValueError(f'{name}/{path} revision mismatch: {actual}')
    return f'{name} commit={commit} source={target}'

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(fetch, item) for item in SOURCES.items()]
        tasks += [pool.submit(fetch_git, name) for name in LOCK]
        for task in tasks:
            result = task.result()
            print(result)
