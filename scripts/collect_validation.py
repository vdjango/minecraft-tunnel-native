"""Fetch explicit, non-secret validation results from the isolated node."""
from pathlib import Path
from remote_validate import ROOT, connect

destination = ROOT / 'validation-artifacts/ubuntu-20261023'
destination.mkdir(parents=True, exist_ok=True)
with connect() as client, client.open_sftp() as sftp:
    for name in ('build-compile.log', 'native-transport.log', 'compatibility.log',
                 'compatibility.status', 'benchmark-ubuntu.json', 'benchmark-ubuntu-final.json',
                 'benchmark-final.log', 'benchmark.status', 'asan.log', 'asan.status',
                 'benchmark-repeat1.json', 'benchmark-repeat2.json', 'benchmark-repeat.status',
                 'install.log', 'install.status', 'installed-transport.log', 'final.status'):
        try:
            sftp.get('/tmp/buggcraft-native-validation/' + name, str(destination / name))
            print('Collected', name)
        except FileNotFoundError:
            pass
