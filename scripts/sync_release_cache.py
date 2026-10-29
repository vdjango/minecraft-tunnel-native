#!/usr/bin/env python3
"""Mirror public GitHub releases and their assets to the static download site."""

from datetime import datetime, timezone
import hashlib
from http.client import RemoteDisconnected
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from time import sleep
from urllib.parse import quote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY = 'vdjango/buggcraft'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases'
DOWNLOAD_ROOT = Path('/opt/buggcraft-site/download')
CACHE_ROOT = DOWNLOAD_ROOT / 'release-cache'
INDEX_PATH = DOWNLOAD_ROOT / 'releases.json'
MAX_CACHE_BYTES = 20 * 1024**3
RESERVED_FREE_BYTES = 10 * 1024**3
USER_AGENT = 'Buggcraft-Release-Cache/1.0'


def request_bytes(url, accept='application/octet-stream'):
    request = Request(url, headers={'Accept': accept, 'User-Agent': USER_AGENT})
    with urlopen(request, timeout=90) as response:
        return response.read()


def fetch_releases():
    releases = []
    for page in range(1, 11):
        url = f'{API_URL}?per_page=100&page={page}'
        values = json.loads(request_bytes(url, 'application/vnd.github+json'))
        if not isinstance(values, list):
            raise ValueError('GitHub returned an invalid releases list')
        releases.extend(item for item in values if not item.get('draft') and item.get('assets'))
        if len(values) < 100:
            break
    return releases


def release_directory(tag):
    if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', tag):
        raise ValueError(f'Invalid release tag: {tag!r}')
    return tag


def asset_filename(name):
    if not isinstance(name, str) or name in ('', '.', '..') or Path(name).name != name:
        raise ValueError(f'Invalid release asset name: {name!r}')
    if any(ord(character) < 32 for character in name):
        raise ValueError(f'Invalid control character in asset name: {name!r}')
    return name


def local_asset_url(tag, name):
    return '/release-cache/{}/{}'.format(quote(tag, safe=''), quote(name, safe=''))


def download_asset(asset, destination):
    asset_id = asset.get('id')
    url = asset.get('url', '')
    parsed = urlparse(url)
    if (not isinstance(asset_id, int) or parsed.scheme != 'https' or parsed.hostname != 'api.github.com'
            or parsed.path != f'/repos/{REPOSITORY}/releases/assets/{asset_id}'):
        raise ValueError('Release asset API URL is outside the configured GitHub repository')
    expected_size = asset.get('size')
    if not isinstance(expected_size, int) or expected_size < 0:
        raise ValueError('Release asset has an invalid size')
    if destination.is_file() and destination.stat().st_size == expected_size:
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        temporary_path = None
        digest = hashlib.sha256()
        received = 0
        try:
            request = Request(url, headers={'Accept': 'application/octet-stream', 'User-Agent': USER_AGENT})
            with urlopen(request, timeout=180) as response:
                final_host = urlparse(response.geturl()).hostname or ''
                if (final_host not in ('api.github.com', 'github.com')
                        and not final_host.endswith('.githubusercontent.com')):
                    raise ValueError('Release asset redirected outside GitHub')
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.release-', delete=False) as output:
                    temporary_path = Path(output.name)
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > expected_size:
                            raise ValueError('Release asset is larger than its published size')
                        digest.update(chunk)
                        output.write(chunk)
            if received != expected_size:
                raise RemoteDisconnected(
                    f'Release asset size mismatch: expected {expected_size}, received {received}')
            published_digest = asset.get('digest')
            if published_digest and published_digest.lower() != 'sha256:' + digest.hexdigest():
                raise ValueError('Release asset SHA-256 does not match GitHub metadata')
            temporary_path.chmod(0o644)
            os.replace(temporary_path, destination)
            return True
        except HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, OSError) as error:
            if attempt == 2:
                raise
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        sleep(2 ** attempt)


def write_index(payload):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=INDEX_PATH.parent,
                                         prefix='.releases-', delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, separators=(',', ':'))
            output.write('\n')
        temporary_path.chmod(0o644)
        os.replace(temporary_path, INDEX_PATH)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def clean_unused_assets(expected_paths):
    for path in CACHE_ROOT.rglob('*'):
        if path.is_symlink():
            raise ValueError('Unexpected symbolic link in release cache')
        if path.is_file() and path.relative_to(CACHE_ROOT).as_posix() not in expected_paths:
            path.unlink()
    for path in sorted(CACHE_ROOT.rglob('*'), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def sync():
    if not DOWNLOAD_ROOT.is_dir() or DOWNLOAD_ROOT.is_symlink():
        raise RuntimeError(f'Download site is not available: {DOWNLOAD_ROOT}')
    if CACHE_ROOT.is_symlink():
        raise RuntimeError('Release cache path must not be a symbolic link')
    CACHE_ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)

    releases = fetch_releases()
    if not releases:
        raise RuntimeError('GitHub returned no public releases; keep the existing cache')
    expected_bytes = 0
    required_bytes = 0
    expected_paths = set()
    for release in releases:
        tag = release_directory(release.get('tag_name'))
        for asset in release['assets']:
            name = asset_filename(asset.get('name'))
            size = asset.get('size')
            if not isinstance(size, int) or size < 0:
                raise ValueError(f'Invalid asset size for {tag}/{name}')
            expected_bytes += size
            destination = CACHE_ROOT / tag / name
            if not destination.is_file() or destination.stat().st_size != size:
                required_bytes += size
            expected_paths.add(f'{tag}/{name}')
    if expected_bytes > MAX_CACHE_BYTES:
        raise RuntimeError(f'Releases require {expected_bytes} bytes; cache limit is {MAX_CACHE_BYTES}')
    free_bytes = shutil.disk_usage(DOWNLOAD_ROOT).free
    if required_bytes > free_bytes - RESERVED_FREE_BYTES:
        raise RuntimeError('Not enough free disk space to update release cache')

    downloaded = 0
    for release in releases:
        tag = release_directory(release.get('tag_name'))
        for asset in release['assets']:
            name = asset_filename(asset.get('name'))
            destination = CACHE_ROOT / tag / name
            if download_asset(asset, destination):
                downloaded += 1
            asset['browser_download_url'] = local_asset_url(tag, name)

    payload = {
        'repository': REPOSITORY,
        'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'releases': releases,
    }
    write_index(payload)
    clean_unused_assets(expected_paths)
    print(json.dumps({
        'updated_at': payload['updated_at'],
        'releases': len(releases),
        'assets': sum(len(release['assets']) for release in releases),
        'downloaded_assets': downloaded,
        'cached_bytes': expected_bytes,
    }))


if __name__ == '__main__':
    sync()
