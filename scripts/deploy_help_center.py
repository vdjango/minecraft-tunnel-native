"""Deploy only the public homepage and tutorial center to the existing web root."""
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time
import uuid

from production_remote import connect

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'

def main():
    paths = [SITE/'index.html', SITE/'sitemap.xml', SITE/'vue-page-home.js']
    paths += list((SITE/'docs').rglob('index.html'))
    paths += [p for p in (SITE/'help-assets').iterdir() if p.is_file()]
    homepage=(SITE/'index.html').read_text(encoding='utf-8')
    paths += [SITE/name for name in set(re.findall(r'/((?:vue-runtime-)[A-Za-z0-9_-]+\.js)',homepage))]
    files={str(p.relative_to(SITE)).replace('\\','/'):p.read_bytes() for p in paths}
    files['.deploy/help.locations.conf']=(ROOT/'packaging/nginx/buggcraft-help.locations.conf').read_bytes()
    files['.deploy/seo_submit.py']=(ROOT/'scripts/seo_submit.py').read_bytes()
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w:gz') as tar:
        for name,data in files.items():
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;item.mtime=int(time.time());tar.addfile(item,io.BytesIO(data))
    remote='/tmp/bugg-help-'+uuid.uuid4().hex+'.tar.gz'
    with connect('node1') as client:
        with client.open_sftp() as sftp:
            with sftp.file(remote,'wb') as output:output.write(archive.getvalue())
        script=r'''
import hashlib,json,os,pathlib,shutil,subprocess,tarfile,tempfile,time
archive=pathlib.Path(ARCHIVE)
root=pathlib.Path('/opt/buggcraft-site')
backup=pathlib.Path('/var/backups/buggcraft-help')/time.strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True,exist_ok=False)
stage=pathlib.Path(tempfile.mkdtemp(prefix='bugg-help-'))
written=[]
try:
 with tarfile.open(archive) as tar:
  for item in tar.getmembers():
   target=(stage/item.name).resolve()
   if not target.is_relative_to(stage) or not item.isfile():raise RuntimeError('Invalid deployment member')
  tar.extractall(stage,filter='data')
 for name,digest in MANIFEST.items():
  if hashlib.sha256((stage/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Deployment checksum mismatch')
 for name in MANIFEST:
  if name.startswith('.deploy/'):continue
  target=root/name
  if target.exists():
   saved=backup/'site'/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
  target.parent.mkdir(parents=True,exist_ok=True)
  temporary=target.with_name(target.name+'.help-tmp');shutil.copy2(stage/name,temporary);os.replace(temporary,target)
  written.append((target,backup/'site'/name))
 targets=[(stage/'.deploy/help.locations.conf',pathlib.Path('/etc/nginx/snippets/buggcraft-help.locations.conf')),
          (stage/'.deploy/seo_submit.py',pathlib.Path('/opt/buggcraft-seo/seo_submit.py'))]
 for source,target in targets:
  saved=backup/'system'/target.relative_to('/');saved.parent.mkdir(parents=True,exist_ok=True)
  if target.exists():shutil.copy2(target,saved)
  shutil.copy2(source,target);written.append((target,saved))
 config=pathlib.Path('/etc/nginx/sites-enabled/buggcraft-domains').resolve()
 original=config.read_text()
 marker='    root /opt/buggcraft-site;'
 include='    include /etc/nginx/snippets/buggcraft-help.locations.conf;'
 if include not in original:
  if original.count(marker)!=1:raise RuntimeError('Unexpected homepage server configuration')
  saved=backup/'nginx-domains.conf';shutil.copy2(config,saved);written.append((config,saved))
  config.write_text(original.replace(marker,include+'\n'+marker))
 subprocess.run(['nginx','-t'],check=True,capture_output=True)
 subprocess.run(['systemctl','reload','nginx'],check=True)
 print(json.dumps({'status':'deployed','files':len(MANIFEST)-2,'backup':str(backup)}))
except Exception:
 for target,saved in reversed(written):
  if saved.exists():shutil.copy2(saved,target)
  elif target.exists():target.unlink()
 subprocess.run(['nginx','-t'],check=True,capture_output=True)
 subprocess.run(['systemctl','reload','nginx'],check=True)
 raise
finally:
 shutil.rmtree(stage);archive.unlink(missing_ok=True)
'''
        script='ARCHIVE='+repr(remote)+'\nMANIFEST='+repr(manifest)+'\n'+script
        stdin,stdout,stderr=client.exec_command('sudo -n python3 -',timeout=55)
        stdin.write(script);stdin.channel.shutdown_write()
        print(stdout.read().decode(),end='')
        error=stderr.read().decode()
        if error:print(error)
        if stdout.channel.recv_exit_status():raise SystemExit(1)

if __name__=='__main__':main()
