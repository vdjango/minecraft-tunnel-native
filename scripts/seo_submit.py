"""Submit changed public pages; accepted submissions do not imply indexing.

Only canonical, content-bearing pages from each site's sitemap are eligible.
Credentials and state belong outside the repository. Python standard library only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, quote, urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

UA = 'BuggcraftSEO/1.0 (+https://bugg.icu/)'
PUBLIC_PATH = re.compile(r'^/(?:docs|developers|network|connect|projects/(?:modrinth|curseforge)/[A-Za-z0-9_-]+)?$')
MAX_URLS = 10000


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name+'.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def fetch(url, *, data=None, headers=None, method=None, limit=4*1024*1024):
    request = Request(url, data=data, headers={'User-Agent': UA, **(headers or {})}, method=method)
    with urlopen(request, timeout=15) as response:
        body = response.read(limit+1)
        if len(body)>limit: raise ValueError('Response too large')
        return response.status, response.geturl(), body


def public_url(url, origin):
    parsed = urlparse(url)
    return (parsed.scheme == 'https' and parsed.netloc == urlparse(origin).netloc
            and not parsed.query and not parsed.fragment and not parsed.username
            and bool(PUBLIC_PATH.fullmatch(parsed.path)))


def sitemap_urls(origin):
    pending, visited, urls = [origin+'/sitemap.xml'], set(), set()
    while pending:
        address = pending.pop()
        if address in visited: continue
        if len(visited)>=32: raise ValueError('Too many sitemaps')
        visited.add(address)
        _, final, body = fetch(address)
        if urlparse(final).netloc != urlparse(origin).netloc: raise ValueError('Cross-site sitemap')
        root = ET.fromstring(body)
        locations = [item.text.strip() for item in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc') if item.text]
        if root.tag.endswith('sitemapindex'):
            for address in locations:
                parsed = urlparse(address)
                if parsed.scheme!='https' or parsed.netloc!=urlparse(origin).netloc or parsed.query or not parsed.path.endswith('.xml'):
                    raise ValueError('Unexpected sitemap address')
                pending.append(address)
        elif root.tag.endswith('urlset'):
            urls.update(url for url in locations if public_url(url, origin))
        else: raise ValueError('Invalid sitemap')
        if len(urls)>MAX_URLS: raise ValueError('Too many public pages')
    return sorted(urls)


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical=''
        self.robots=''
        self.description=''
        self.title=[]
        self.text=[]
        self.main=0
        self.skip=0
        self.in_title=False
        self.h1=False

    def handle_starttag(self, tag, attributes):
        a=dict(attributes)
        if tag=='link' and a.get('rel')=='canonical': self.canonical=a.get('href','')
        if tag=='meta' and a.get('name')=='robots': self.robots=a.get('content','')
        if tag=='meta' and a.get('name')=='description': self.description=a.get('content','')
        if tag=='title': self.in_title=True
        if tag=='main': self.main+=1
        if tag in ('script','style'): self.skip+=1
        if tag=='h1': self.h1=True

    def handle_endtag(self, tag):
        if tag=='main': self.main=max(0,self.main-1)
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        if tag=='title': self.in_title=False

    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if self.main and not self.skip: self.text.append(data)


def page_fingerprint(url):
    try:
        _, final, body = fetch(url)
        page=Page()
        page.feed(body.decode('utf-8'))
        text=' '.join(' '.join(page.text).split())
        if final!=url or page.canonical!=url or 'noindex' in page.robots.lower():
            return url, None, 'not_canonical_or_noindex'
        if not page.h1 or len(text)<80 or not page.description:
            return url, None, 'insufficient_content'
        digest=hashlib.sha256(json.dumps([page.title,page.description,text], ensure_ascii=False).encode()).hexdigest()
        return url, digest, ''
    except Exception as error:
        # Error strings can contain credential-bearing URLs; never log them.
        return url, None, 'HTTP '+str(error.code) if isinstance(error,HTTPError) else type(error).__name__


def provider_key(site, provider):
    # Changing ownership keys/endpoints must invalidate previous acknowledgements.
    value=site.get('indexnow_key','') if provider=='indexnow' else site.get(provider,{})
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def submit(provider, site, urls):
    origin=site['origin']
    if provider=='indexnow':
        key=site.get('indexnow_key','')
        if not re.fullmatch(r'[a-zA-Z0-9-]{8,128}',key): return {'status':'not_configured'}
        location=origin+'/'+site['indexnow_file']
        _, final, body=fetch(location,limit=256)
        if final!=location or body.decode().strip()!=key: raise ValueError('Ownership key unavailable')
        data=json.dumps({'host':urlparse(origin).netloc,'key':key,'keyLocation':location,'urlList':urls}).encode()
        code, _, _=fetch('https://api.indexnow.org/indexnow',data=data,headers={'Content-Type':'application/json; charset=utf-8'})
        return {'status':'accepted' if code==200 else 'ownership_pending' if code==202 else 'rejected','http_status':code,'urls':len(urls)}
    if provider=='google':
        settings=site.get('google',{})
        if not settings: return {'status':'authorization_required'}
        token=settings.get('access_token')
        if settings.get('refresh_token'):
            payload=urlencode({key:settings[key] for key in ('client_id','client_secret','refresh_token') }|{'grant_type':'refresh_token'}).encode()
            _,_,body=fetch('https://oauth2.googleapis.com/token',data=payload,headers={'Content-Type':'application/x-www-form-urlencoded'})
            token=json.loads(body)['access_token']
        if not token: return {'status':'authorization_required'}
        property=settings.get('site_url',origin+'/')
        endpoint='https://www.googleapis.com/webmasters/v3/sites/'+quote(property,safe='')+'/sitemaps/'+quote(origin+'/sitemap.xml',safe='')
        code,_,_=fetch(endpoint,data=b'',method='PUT',headers={'Authorization':'Bearer '+token})
        return {'status':'accepted','http_status':code,'type':'sitemap'}
    if provider=='baidu':
        endpoint=site.get('baidu',{}).get('submit_url','')
        if not endpoint: return {'status':'authorization_required'}
        if urlparse(endpoint).scheme!='https' or urlparse(endpoint).hostname!='data.zz.baidu.com':
            raise ValueError('Invalid Baidu endpoint')
        code,_,body=fetch(endpoint,data='\n'.join(urls).encode(),headers={'Content-Type':'text/plain; charset=utf-8'})
        result=json.loads(body)
        if result.get('error'): raise ValueError('Baidu rejected request')
        accepted=int(result.get('success',0))
        return {'status':'accepted' if accepted==len(urls) else 'partial','http_status':code,'urls':accepted,'remaining':result.get('remain')}
    raise ValueError('Unknown provider')


def import_confirmed_indexnow(state, config, path):
    """Import a validated one-time retry batch into durable state."""
    path=Path(path)
    if not path.is_file(): return
    result=json.loads(path.read_text())
    origin=result.get('origin')
    site=next((s for s in config['sites'] if s['origin']==origin),None)
    if not site or result.get('status')!='accepted' or result.get('http_status')!=200:
        return
    previous=state.setdefault(origin+'|indexnow|'+provider_key(site,'indexnow'),{'acknowledged':{}})
    previous.update(batch_submitted_at=result['submitted_at'],failures=0,retry_after=0)
    path.rename(path.with_suffix('.imported.json'))


def run(config, state, *, force=False, checkpoint=None):
    report={'started_at':utc(),'sites':[],'meaning':'Accepted by a submission API; search indexing is not confirmed.'}
    for site in config['sites']:
        origin=site['origin'].rstrip('/')
        if urlparse(origin).scheme!='https' or urlparse(origin).path: raise ValueError('Expected HTTPS origin')
        row={'origin':origin,'providers':{},'skipped':[]}
        report['sites'].append(row)
        if checkpoint: checkpoint(report)
        try:
            urls=sitemap_urls(origin)
            # Large catalogues advance a durable cursor instead of fetching every
            # project on every timer tick. Public landing pages are always checked.
            landing=[url for url in urls if '/projects/' not in url]
            projects=[url for url in urls if '/projects/' in url]
            scan=state.setdefault(origin+'|scan',{'cursor':0})
            budget=max(1,min(256,int(site.get('max_pages_per_run',64))))
            cursor=scan['cursor']%len(projects) if projects else 0
            batch=projects[cursor:cursor+max(0,budget-len(landing))]
            scan['cursor']=0 if cursor+len(batch)>=len(projects) else cursor+len(batch)
            with ThreadPoolExecutor(max_workers=4) as pool: checked=list(pool.map(page_fingerprint,landing+batch))
            eligible={url:digest for url,digest,error in checked if digest}
            row.update(discovered=len(urls),checked=len(checked),eligible=len(eligible),remaining_in_cycle=max(0,len(projects)-cursor-len(batch)))
            row['skipped']=[{'url':url,'reason':error} for url,digest,error in checked if error]
            if checkpoint: checkpoint(report)
        except Exception as error:
            row['error']='HTTP '+str(error.code) if isinstance(error,HTTPError) else type(error).__name__
            if checkpoint: checkpoint(report)
            continue
        for provider in ('indexnow','google','baidu'):
            identity=origin+'|'+provider+'|'+provider_key(site,provider)
            previous=state.setdefault(identity,{'acknowledged':{}})
            if provider=='indexnow' and previous.get('batch_submitted_at') and not previous.get('batch_scan_complete'):
                previous['acknowledged'].update(eligible)
                if scan['cursor']==0:previous['batch_scan_complete']=True
                row['providers'][provider]={'status':'prior_batch_confirmed','urls_checked':len(eligible)}
                if checkpoint:checkpoint(report)
                continue
            changed=[url for url,digest in eligible.items() if force or previous['acknowledged'].get(url)!=digest]
            if not changed:
                row['providers'][provider]={'status':'unchanged','urls':0}
                if checkpoint: checkpoint(report)
                continue
            if not force and previous.get('retry_after',0)>time.time():
                row['providers'][provider]={'status':'retry_scheduled','retry_after':previous['retry_after']}
                if checkpoint: checkpoint(report)
                continue
            try:
                result=submit(provider,site,changed)
                row['providers'][provider]=result
                if result['status'] in ('accepted','ownership_pending'):
                    previous['acknowledged'].update(eligible)
                    previous['acknowledged']={url:digest for url,digest in previous['acknowledged'].items() if url in urls}
                    previous.update(failures=0,retry_after=0,submitted_at=utc())
                elif result['status']=='partial': raise ValueError('Partial submission; retry later')
            except Exception as error:
                count=previous.get('failures',0)+1
                previous.update(failures=count,retry_after=time.time()+min(86400,300*2**min(count-1,8)))
                row['providers'][provider]={'status':'retry_scheduled','reason':'HTTP '+str(error.code) if isinstance(error,HTTPError) else type(error).__name__,'retry_after':previous['retry_after']}
            if checkpoint: checkpoint(report)
    report['finished_at']=utc()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args()
    # The service has one process; protect against concurrent manual invocations.
    import fcntl
    args.state.parent.mkdir(parents=True,exist_ok=True)
    with args.state.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=json.loads(args.state.read_text()) if args.state.exists() else {}
        def checkpoint(report):
            write_json(args.state,state)
            write_json(args.report,report)
        config=json.loads(args.config.read_text())
        import_confirmed_indexnow(state,config,args.state.parent/'translation-batch-result.json')
        write_json(args.state,state)
        report=run(config,state,force=args.force,checkpoint=checkpoint)
        write_json(args.state,state)
        write_json(args.report,report)
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
