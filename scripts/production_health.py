"""Read-only production health and journal checks; output only safe counters."""
import concurrent.futures
import json
from pathlib import Path

from production_remote import connect
from production_release import remote_python, save, RELEASE, TAG

SCRIPT = r'''
import collections,configparser,hashlib,http.cookiejar,json,pathlib,re,ssl,subprocess,tarfile,urllib.request
data=pathlib.Path('/var/lib/buggcraft-node');backup=pathlib.Path('/var/backups/buggcraft-native-'+TAG)
config=configparser.ConfigParser(interpolation=None);config.read(data/'server.conf')
base='https://127.0.0.1:'+config['web'].get('port','18443')
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
 urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
def get(path):
 return json.loads(opener.open(base+path,timeout=5).read())
status=get('/api/status')
state=json.loads((data/'node-state.json').read_text())
credentials=(data/'admin-credentials.txt').read_text()
password=re.search(r'^Admin password: (.+)$',credentials,re.M).group(1)
request=urllib.request.Request(base+'/api/login',data=json.dumps(dict(username=state.get('admin_user','admin'),
 password=password)).encode(),headers={'Content-Type':'application/json'})
authentication=json.loads(opener.open(request,timeout=5).read())
diagnostics=get('/api/admin/diagnostics')
assert diagnostics['write_errors']==0 and diagnostics['dropped_events']==0
assert diagnostics['retention_days']==7 and diagnostics['max_bytes']==1024**3
logout=urllib.request.Request(base+'/api/logout',data=b'{}',headers={'Content-Type':'application/json',
 'X-CSRF-Token':authentication['csrf']})
opener.open(logout,timeout=5).read()
started=(backup/'deployment-result.json').stat().st_mtime
paths=[p for p in (data/'logs/players').glob('*/*.jsonl') if p.stat().st_mtime>=started]
events=collections.Counter();invalid=0;traffic=0
for path in paths:
 assert path.stat().st_mode&0o777==0o600
 for line in path.read_text().splitlines():
  try:
   row=json.loads(line);events[row.get('event','unknown')]+=1
   if row.get('event')=='stream_closed': traffic+=row.get('upload_bytes',0)+row.get('download_bytes',0)
  except (ValueError,TypeError): invalid+=1
assert paths and events['connected'] and events['disconnected'] and events['stream_closed']
assert invalid==0 and traffic>=2*1024**2
# Check persistent accounts, mail settings, administrator and federation identity
# against the private pre-upgrade backup without exporting any values or hashes.
with tarfile.open(backup/'persistent-data.tar.gz') as archive:
 old_state=json.load(archive.extractfile('buggcraft-node/node-state.json'))
 preserved_keys=[k for k in old_state if any(s in k for s in ('admin','mail','network_key'))]
 assert all(old_state[k]==state.get(k) for k in preserved_keys)
 account_files=[m for m in archive.getmembers() if m.isfile() and m.name.startswith('buggcraft-node/accounts')]
 for member in account_files:
  relative=pathlib.Path(member.name).relative_to('buggcraft-node')
  assert archive.extractfile(member).read()==(data/relative).read_bytes()
unit=subprocess.check_output(['systemctl','show','buggcraft-node','-p','ActiveState','-p','SubState',
 '-p','NRestarts','-p','UnitFileState'],text=True)
assert 'ActiveState=active' in unit and 'UnitFileState=enabled' in unit and 'NRestarts=0' in unit
journal=subprocess.check_output(['journalctl','-u','buggcraft-node','--since','@'+str(int(started)),
 '--no-pager','-o','cat'],text=True)
flags={key:journal.count(key) for key in ('Traceback (most recent call last)',
 'native_core_exited','native_control_overflow','Player diagnostic journal unavailable','ERROR')}
assert not any(flags.values()), 'Service error present; inspect privately'
assert status['network_nodes']==2 and all(n['status']=='online' for n in status['nodes'])
assert status['players']==0 and status['rooms']==0 and status['streams']==0
binary=pathlib.Path(RELEASE)/'bin/buggcraft-core'
print(json.dumps(dict(status={k:status.get(k) for k in ('status','players','rooms','streams','network_nodes')},
 systemd=unit,core_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
 new_player_journal_files=len(paths),journal_events=dict(events),closed_stream_traffic_bytes=traffic,
 journal_invalid_lines=invalid,journal_files_mode='0600',journal_error_counts=flags,
 diagnostics={k:diagnostics[k] for k in ('retention_days','max_bytes','dropped_events','write_errors')},
 preserved_settings=True,preserved_account_files=len(account_files))))
'''


def check(node):
    with connect(node) as client:
        script='RELEASE='+repr(RELEASE)+'\nTAG='+repr(TAG)+'\n'+SCRIPT
        result=json.loads(remote_python(client,script))
        save(node+'-health.json',result)
        print(json.dumps(dict(node=node,**result)),flush=True)


if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(check,('node1','node2')))
