"""Read-only deployment inventory. Emit only explicitly public fields."""
import configparser
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import ssl

data = Path('/var/lib/buggcraft-node')
config = configparser.ConfigParser(interpolation=None)
config.read(data / 'server.conf')
state = json.loads((data / 'node-state.json').read_text())
relay, web = config['relay'], config['web']
status = json.loads(urllib.request.urlopen(
    'https://127.0.0.1:' + web.get('port', '18443') + '/api/status',
    context=ssl._create_unverified_context(), timeout=5).read())
print(json.dumps(dict(host=os.uname().nodename,
    os=Path('/etc/os-release').read_text().splitlines()[:2],
    uid=os.geteuid(), current=os.path.realpath('/opt/buggcraft-node/current'),
    service=subprocess.check_output(['systemctl', 'show', 'buggcraft-node',
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'NRestarts'], text=True),
    relay={key: relay.get(key) for key in ('host', 'port', 'entry_host', 'entry_port', 'workers', 'buffer_kib', 'compression')},
    web={key: web.get(key) for key in ('host', 'port')}, name=state['name'],
    seed_count=len(state.get('seeds', [])),
    public_status={key: status.get(key) for key in ('status', 'players', 'rooms', 'network_nodes', 'streams')},
    unit=subprocess.check_output(['systemctl', 'cat', 'buggcraft-node'], text=True),
    diagnostic_files=len(list((data / 'logs/players').glob('*/*.jsonl'))),
    disk=subprocess.check_output(['df', '-h', '/opt', '/var/lib'], text=True)), ensure_ascii=True))
