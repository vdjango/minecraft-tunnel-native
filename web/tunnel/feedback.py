"""Federated issue records and private, bounded diagnostic attachments."""
import asyncio
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time

import asyncssh
from aiohttp import web
from .federation import canonical

LOG = logging.getLogger(__name__)
ID = re.compile('[0-9a-f]{32}')
STATUSES = {'open', 'accepted', 'investigating', 'resolved'}
MAX_FILE = 20 * 1024**2
MAX_STORAGE = 1024**3


def text(value, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('请检查必填内容和字数限制。')
    return ''.join(c for c in value.strip() if c.isprintable() or c in '\n\t')


class Feedback:
    def __init__(self, portal):
        self.portal, self.fed = portal, portal.federation
        self.root = portal.path.parent / 'feedback'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'events.jsonl'
        self.events = {}
        self.by_issue = {}
        self.lock = asyncio.Lock()
        self.transfers = asyncio.Semaphore(2)
        self.sends = asyncio.Semaphore(2)
        self.uploads = asyncio.Semaphore(2)
        self.sync_lock = asyncio.Lock()
        self.peer_versions = {}
        self.sync_errors = 0
        self.last_sync = None
        self.stopping = False
        self.cleanup_pending = set()
        if self.path.exists():
            with self.path.open(encoding='utf-8') as journal:
                for line in journal:
                    try:
                        event = json.loads(line)
                        self.events[event['body']['id']] = event
                        self.by_issue.setdefault(event['body']['issue'], []).append(event)
                    except (ValueError, KeyError, TypeError):
                        LOG.warning('Skipped an incomplete feedback journal record')
        self.needs_newline = False
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open('rb') as source:
                source.seek(-1, os.SEEK_END)
                self.needs_newline = source.read(1) != b'\n'
        self.fed.feedback = self
        # Incomplete uploads are never published. Remove remnants left by a
        # previous crash before accepting any new transfer.
        for temporary in self.root.glob('*/*.part*'):
            temporary.unlink(missing_ok=True)
        self.routes = [web.get('/feedback', self.page), web.get('/feedback/{issue:[0-9a-f]{32}}', self.page),
                       web.get('/admin/feedback', self.page), web.get('/feedback.js', portal.asset),
                       web.get('/api/feedback', self.list_issues), web.post('/api/feedback', self.create),
                       web.get('/api/feedback/{issue}', self.detail),
                       web.post('/api/feedback/{issue}/comments', self.comment),
                       web.post('/api/feedback/{issue}/status', self.status),
                       web.post('/api/feedback/{issue}/attachments', self.upload),
                       web.get('/api/feedback/{issue}/attachments/{attachment}', self.download)]

    def viewer(self, request, write=False, admin=False):
        if not admin and request.headers.get('X-Feedback-Role') != 'admin' and request.cookies.get('account_session'):
            session = self.portal.account_api.session(request, write)
            return {'admin': False, 'account': session['account'],
                    'username': self.portal.account_api.store.social.name(session['account'])}
        try:
            self.portal.session(request, write)
            return {'admin': True, 'account': '', 'username': '节点管理员'}
        except web.HTTPUnauthorized:
            if admin:
                raise
        session = self.portal.account_api.session(request, write)
        return {'admin': False, 'account': session['account'],
                'username': self.portal.account_api.store.social.name(session['account'])}

    def issue(self, issue):
        if not isinstance(issue, str) or not ID.fullmatch(issue):
            raise web.HTTPNotFound(text='问题不存在。')
        records = sorted([e['body'] for e in self.by_issue.get(issue, ()) if
                          e['body'].get('audience') == self.fed.proof('feedback-audience')],
                         key=lambda e: (e['at'], e['id']))
        created = next((e for e in records if e['kind'] == 'created'), None)
        if not created:
            raise web.HTTPNotFound(text='问题尚未同步或不存在。')
        closed = any(e['kind'] == 'status' and e['data']['status'] == 'resolved' for e in records)
        status = 'resolved' if closed else next((e['data']['status'] for e in reversed(records) if e['kind'] == 'status'), 'open')
        attachments = [dict(e['data'], id=e['id'], node=e['node'], deleted=closed)
                       for e in records if e['kind'] == 'attachment']
        return dict(id=issue, **created['data'], reporter=created['username'], account=created['actor'],
                    created=created['at'], updated=records[-1]['at'], status=status,
                    history=records, attachments=attachments)

    def snapshot(self):
        issues = {e['body']['issue'] for e in self.events.values() if e['body']['kind'] == 'created' and
                  e['body'].get('audience') == self.fed.proof('feedback-audience')}
        return sorted((self.issue(i) for i in issues), key=lambda i: i['updated'], reverse=True)

    def persist(self, event):
        with self.path.open('a', encoding='utf-8') as target:
            if self.needs_newline:
                target.write('\n')
            target.write(json.dumps(event,ensure_ascii=False)+'\n')
            target.flush()
            os.fsync(target.fileno())
        self.needs_newline = False
        self.path.chmod(0o600)

    def add(self, issue, kind, data, actor):
        if len(self.events) >= 20000 or len(self.by_issue.get(issue, ())) >= 100:
            raise ValueError('反馈记录已达容量上限，请联系管理员。')
        body = dict(id=secrets.token_hex(16), issue=issue, node=self.fed.id, at=time.time(), audience=self.fed.proof('feedback-audience'),
                    kind=kind, data=data, actor=actor['account'], username=actor['username'], admin=actor['admin'])
        event = dict(body=body, signature=base64.b64encode(self.fed.key.sign(canonical(body), b'ssh-ed25519')).decode())
        self.persist(event)
        self.events[body['id']] = event
        self.by_issue.setdefault(issue, []).append(event)
        self.cleanup(issue)
        return body

    def ingest(self, event):
        body = event['body']
        if not isinstance(body, dict) or len(canonical(event)) > 30000 or len(self.events) >= 20000:
            raise ValueError('Invalid feedback event')
        if body.get('id') in self.events:
            return False
        if (set(body) != {'id', 'issue', 'node', 'at', 'kind', 'data', 'actor', 'username', 'admin', 'audience'} or
                body['audience'] != self.fed.proof('feedback-audience') or
                not ID.fullmatch(body['id']) or not ID.fullmatch(body['issue']) or
                type(body['at']) not in (float, int) or not 0 < body['at'] <= time.time()+5 or
                type(body['admin']) is not bool or not isinstance(body['data'], dict)):
            raise ValueError('Invalid feedback fields')
        node = body['node']
        profile = self.fed.profile if node == self.fed.id else self.fed.records.get(node, {}).get('body', {}).get('profile')
        if not profile or node in self.fed.blocked:
            raise ValueError('Untrusted feedback signer')
        public = asyncssh.import_public_key(profile['server_public_key'])
        if not public.verify(canonical(body), base64.b64decode(event['signature'], validate=True)):
            raise ValueError('Invalid feedback signature')
        text(body['username'], 80)
        if not isinstance(body['actor'], str) or (not body['admin'] and not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{32}', body['actor'])):
            raise ValueError('Invalid reporter')
        self.validate_data(body['kind'], body['data'], body['admin'])
        if body['kind'] != 'created':
            issue = self.issue(body['issue'])
            if body['kind'] == 'attachment' and (body['admin'] or body['actor'] != issue['account']):
                raise ValueError('Only the reporter may attach logs')
        else:
            if any(e['body']['kind'] == 'created' for e in self.by_issue.get(body['issue'], ())):
                raise ValueError('Duplicate issue creation')
        if len(self.by_issue.get(body['issue'], ())) >= 100:
            raise ValueError('Too many issue events')
        self.persist(event)
        self.events[body['id']] = event
        self.by_issue.setdefault(body['issue'], []).append(event)
        self.cleanup(body['issue'])
        return True

    @staticmethod
    def validate_data(kind, data, admin):
        if kind == 'created':
            if set(data) != {'title', 'description', 'game_version', 'launcher_version', 'system', 'loader'}:
                raise ValueError('Invalid issue')
            for key, limit in [('title',120), ('description',4000), ('game_version',64),
                               ('launcher_version',64), ('system',120), ('loader',120)]:
                text(data[key], limit, required=key in ('title', 'description', 'game_version'))
        elif kind == 'comment':
            if set(data) != {'message'}:
                raise ValueError('Invalid comment')
            text(data['message'],4000)
        elif kind == 'status':
            if not admin or set(data) != {'status','message'} or data['status'] not in STATUSES - {'open'}:
                raise ValueError('Invalid issue status')
            text(data['message'],4000)
        elif kind == 'attachment':
            if (set(data) != {'name','size','sha256'} or type(data['size']) is not int or not 0 < data['size'] <= MAX_FILE or
                    not isinstance(data['sha256'],str) or not re.fullmatch('[0-9a-f]{64}', data['sha256'])):
                raise ValueError('Invalid attachment')
            text(data['name'],120)
        else:
            raise ValueError('Invalid feedback kind')

    def cleanup(self, issue):
        try:
            closed = self.issue(issue)['status'] == 'resolved'
        except web.HTTPNotFound:
            return
        if closed:
            try:
                for path in (self.root / issue).glob('*.bin'):
                    path.unlink(missing_ok=True)
                self.cleanup_pending.discard(issue)
            except OSError:
                self.cleanup_pending.add(issue)
                LOG.warning('Resolved feedback attachment deletion pending; will retry')

    def file(self, issue, attachment):
        item = self.issue(issue)
        entry = next((a for a in item['attachments'] if a['id'] == attachment), None)
        if not entry or entry['deleted']:
            raise web.HTTPGone(text='日志已随问题完成而删除，处理记录仍保留。')
        return self.root / issue / (attachment + '.bin'), entry

    def rpc(self, request):
        op = request['op']
        if op == 'feedback_index':
            offset = request.get('offset', 0)
            if type(offset) is not int or not 0 <= offset <= 20000:
                raise ValueError('Invalid feedback page')
            ids = sorted((i for i,e in self.events.items() if e['body'].get('audience') == self.fed.proof('feedback-audience')),
                         key=lambda i:(self.events[i]['body']['kind']!='created',self.events[i]['body']['at'],i))
            return {'digest': hashlib.sha256(canonical(ids)).hexdigest(), 'ids': ids[offset:offset+100], 'total': len(ids)}
        if op == 'feedback_event':
            event = self.events.get(request.get('id'))
            return {'event': event if event and event['body'].get('audience') == self.fed.proof('feedback-audience') else None}
        raise ValueError('Invalid feedback operation')

    def peers(self):
        result = dict(self.fed.seeds)
        result.update({n:r['body']['profile'] for n,r in self.fed.live().items()})
        return {n:p for n,p in result.items() if n != self.fed.id and n not in self.fed.blocked}

    async def sync_once(self):
        if self.sync_lock.locked():
            return
        async with self.sync_lock:
            for node, profile in self.peers().items():
                if self.stopping:
                    break
                try:
                    async with asyncio.timeout(30):
                        index = await self.fed.account_rpc(profile, 'feedback_index')
                        digest = index['digest']
                        if index['digest'] == self.peer_versions.get(node):
                            continue
                        missing = []
                        total = index['total']
                        if type(total) is not int or not 0 <= total <= 20000:
                            raise ValueError('Invalid feedback inventory')
                        for offset in range(0,max(1,total),100):
                            if offset:
                                index = await self.fed.account_rpc(profile,'feedback_index',offset=offset)
                            if (index['digest'] != digest or not isinstance(index['ids'], list) or len(index['ids']) > 100 or
                                    any(not isinstance(i,str) or not ID.fullmatch(i) for i in index['ids'])):
                                raise ValueError('Feedback inventory changed or is invalid')
                            missing.extend(i for i in index['ids'] if i not in self.events)
                        # Created records precede dependent events, regardless
                        # of the random ID ordering used by the inventory.
                        fetched=[]
                        for identifier in missing[:200]:
                            result=await self.fed.account_rpc(profile,'feedback_event',id=identifier)
                            if result.get('event'): fetched.append(result['event'])
                        fetched.sort(key=lambda e:(e['body']['kind']!='created',e['body']['at'],e['body']['id']))
                        failed=False
                        for event in fetched:
                            try:
                                self.ingest(event)
                            except (ValueError,KeyError,TypeError,web.HTTPException):
                                failed=True
                        if len(missing)<=200 and not failed:
                            self.peer_versions[node]=digest
                        self.last_sync=time.time()
                except (OSError,ValueError,KeyError,TypeError,asyncssh.Error,asyncio.TimeoutError):
                    self.sync_errors+=1

    async def ensure_file(self, issue, attachment):
        path, entry = self.file(issue,attachment)
        if path.exists(): return path
        async with self.transfers:
            if path.exists(): return path
            if sum(p.stat().st_size for p in self.root.glob('*/*.bin')) + entry['size'] > MAX_STORAGE:
                raise web.HTTPInsufficientStorage(text='节点日志存储已满。')
            for profile in self.peers().values():
                temporary = path.with_suffix('.part-'+secrets.token_hex(5))
                try:
                    async with asyncio.timeout(120):
                        public=asyncssh.import_public_key(profile['server_public_key'])
                        async with asyncssh.connect(profile['server_host'],profile['server_port'],username='federation:'+self.fed.id,
                            known_hosts=([public],[],[]),client_keys=[self.fed.key],agent_path=None,config=None,
                            connect_timeout=3,login_timeout=3,x509_trusted_certs=None,x509_trusted_cert_paths=[]) as conn:
                            async with conn.create_process(subsystem='buggcraft-feedback-file-v1',encoding=None) as process:
                                process.stdin.write(json.dumps({'issue':issue,'attachment':attachment}).encode()+b'\n')
                                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                                digest,size=hashlib.sha256(),0
                                with temporary.open('wb') as output:
                                    temporary.chmod(0o600)
                                    while True:
                                        chunk=await asyncio.wait_for(process.stdout.read(65536),15)
                                        if not chunk:break
                                        size+=len(chunk)
                                        if size>entry['size']:raise ValueError('Attachment size mismatch')
                                        digest.update(chunk)
                                        await asyncio.to_thread(output.write,chunk)
                                if size!=entry['size'] or digest.hexdigest()!=entry['sha256']:
                                    raise ValueError('Attachment checksum mismatch')
                                self.file(issue,attachment) # Refuse publication after resolution.
                                os.replace(temporary,path)
                                return path
                except (OSError,ValueError,asyncssh.Error,asyncio.TimeoutError,web.HTTPException):
                    pass
                finally:
                    temporary.unlink(missing_ok=True)
        raise web.HTTPServiceUnavailable(text='日志副本暂不可用，请稍后重试；问题记录仍可查看。')

    async def lifecycle(self, app):
        async def sync():
            while not self.stopping:
                await self.sync_once()
                if self.stopping:break
                await asyncio.sleep(5)
        async def replicate_files():
            while not self.stopping:
                # Replicate attachments independently of metadata; retry after
                # failures, and never re-fetch files from resolved issues.
                for issue in self.snapshot():
                    if self.stopping:break
                    if issue['status'] == 'resolved':
                        self.cleanup(issue['id']);continue
                    for attachment in issue['attachments']:
                        try:
                            await self.ensure_file(issue['id'],attachment['id'])
                        except web.HTTPException:
                            continue
                if not self.stopping:await asyncio.sleep(5)
        tasks=[asyncio.create_task(sync()),asyncio.create_task(replicate_files())]
        try:yield
        finally:
            self.stopping=True
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def page(self, request):
        return self.portal.support_page('feedback.html', 'feedback' if request.path.startswith('/admin/') else None)

    async def list_issues(self, request):
        viewer=self.viewer(request)
        page=int(request.query.get('page','0'))
        if not 0<=page<=1000:raise ValueError('Invalid page')
        issues=self.snapshot()
        if request.query.get('mine')=='1':issues=[i for i in issues if i['account']==viewer.get('account')]
        query=text(request.query.get('q',''),120,False).casefold()
        status=request.query.get('status','')
        if status and status not in STATUSES:raise ValueError('Invalid feedback status')
        issues=[i for i in issues if (not status or i['status']==status) and
                (not query or query in (i['title']+' '+i['reporter']+' '+i['game_version']).casefold())]
        rows=[{k:v for k,v in i.items() if k not in ('history','attachments','account','description')} for i in issues[page*30:page*30+30]]
        return web.json_response({'issues':rows,'total':len(issues),'viewer':viewer,'last_sync':self.last_sync,
                                  'cleanup_pending':len(self.cleanup_pending) if viewer['admin'] else None})

    async def detail(self, request):
        viewer=self.viewer(request)
        issue=self.issue(request.match_info['issue'])
        # Filenames can contain private paths/usernames: disclose attachments
        # to operators and their uploader only.
        if not viewer['admin'] and viewer['account']!=issue['account']:
            issue['attachments']=[]
            issue['history']=[e for e in issue['history'] if e['kind']!='attachment']
        return web.json_response({'issue':issue,'viewer':viewer})

    async def create(self, request):
        viewer=self.viewer(request,True)
        if viewer['admin']:raise web.HTTPForbidden(text='请用用户账号提交问题。')
        self.portal.throttle(request)
        data=await request.json()
        value={k:text(data.get(k,''),n,k in ('title','description','game_version')) for k,n in
               [('title',120),('description',4000),('game_version',64),('launcher_version',64),('system',120),('loader',120)]}
        async with self.lock:
            issues=self.snapshot()
            if len(issues)>=2000 or sum(i['account']==viewer['account'] and i['status']!='resolved' for i in issues)>=10:
                raise web.HTTPTooManyRequests(text='未完成反馈已达上限，请先跟进已有问题。')
            issue=secrets.token_hex(16)
            self.add(issue,'created',value,viewer)
        return web.json_response({'id':issue},status=201)

    async def comment(self, request):
        viewer=self.viewer(request,True)
        self.portal.throttle(request)
        issue=self.issue(request.match_info['issue'])
        data={'message':text((await request.json()).get('message'),4000)}
        async with self.lock:self.add(issue['id'],'comment',data,viewer)
        return web.json_response({'ok':True})

    async def status(self, request):
        viewer=self.viewer(request,True,admin=True)
        issue=self.issue(request.match_info['issue'])
        data=await request.json()
        value={'status':data.get('status'),'message':text(data.get('message'),4000)}
        self.validate_data('status',value,True)
        async with self.lock:
            if issue['status']=='resolved':raise web.HTTPConflict(text='此问题已完成。日志已删除，处理记录保留。')
            self.add(issue['id'],'status',value,viewer)
        return web.json_response({'ok':True,'attachments_deleted':value['status']=='resolved' and issue['id'] not in self.cleanup_pending})

    async def upload(self, request):
        viewer=self.viewer(request,True)
        issue=self.issue(request.match_info['issue'])
        if viewer['admin'] or viewer['account']!=issue['account']:
            raise web.HTTPForbidden(text='只能上传自己提交的问题日志。')
        if issue['status']=='resolved':raise web.HTTPGone(text='问题已完成，不再接收日志。')
        if len(issue['attachments'])>=5:raise web.HTTPTooManyRequests(text='每个问题最多上传 5 个附件。')
        async with self.uploads:
            reader=await request.multipart()
            part=await reader.next()
            if not part or part.name!='file':raise ValueError('请选择日志文件。')
            name=text(Path((part.filename or '').replace('\\','/')).name,120)
            if Path(name).suffix.lower() not in ('.log','.txt','.json','.zip'):
                raise web.HTTPBadRequest(text='支持 .log、.txt、.json 和 .zip，每个文件最多 20 MB。')
            directory=self.root/issue['id'];directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            temporary=directory/(secrets.token_hex(16)+'.part')
            size,digest=0,hashlib.sha256()
            try:
                with temporary.open('wb') as output:
                    temporary.chmod(0o600)
                    async with asyncio.timeout(120):
                        while True:
                            chunk=await part.read_chunk(65536)
                            if not chunk:break
                            size+=len(chunk)
                            if size>MAX_FILE:raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE,actual_size=size)
                            digest.update(chunk)
                            await asyncio.to_thread(output.write,chunk)
                if not size:raise ValueError('不能上传空文件。')
                async with self.lock:
                    issue=self.issue(issue['id'])
                    if issue['status']=='resolved':raise web.HTTPGone(text='问题已完成，附件未保留。')
                    if len(issue['attachments'])>=5:raise web.HTTPTooManyRequests()
                    if sum(p.stat().st_size for p in self.root.glob('*/*.bin'))+size>MAX_STORAGE:
                        raise web.HTTPInsufficientStorage(text='节点日志存储已满。')
                    event=self.add(issue['id'],'attachment',{'name':name,'size':size,'sha256':digest.hexdigest()},viewer)
                    os.replace(temporary,directory/(event['id']+'.bin'))
                return web.json_response({'id':event['id']},status=201)
            finally:temporary.unlink(missing_ok=True)

    async def download(self, request):
        self.viewer(request,admin=True)
        path=await self.ensure_file(request.match_info['issue'],request.match_info['attachment'])
        _,entry=self.file(request.match_info['issue'],request.match_info['attachment'])
        from urllib.parse import quote
        return web.FileResponse(path,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(entry['name']),
                                             'Content-Type':'application/octet-stream'})


class FeedbackFileSession:
    """Read-only, authenticated federation file stream; no game credentials."""
    def __init__(self, peer, channel):
        self.peer,self.channel=peer,channel
        self.buffer=bytearray()
        self.paused=False
        self.task=None
        self.closed=False

    def start(self):pass

    def data_received(self, data, datatype):
        if self.task or datatype is not None or len(self.buffer)+len(data)>1024:
            self.close();return
        self.buffer.extend(data)
        if b'\n' in self.buffer:
            try:
                request=json.loads(self.buffer)
                self.task=asyncio.create_task(self.send(request))
            except (ValueError,TypeError):self.close()

    async def send(self, request):
        try:
            store=self.peer.relay.federation.feedback
            async with store.sends:
                path,_=store.file(request['issue'],request['attachment'])
                with path.open('rb') as source:
                    async with asyncio.timeout(120):
                        while not self.closed:
                            while self.paused and not self.closed:await asyncio.sleep(.02)
                            store.file(request['issue'],request['attachment'])
                            chunk=await asyncio.to_thread(source.read,65536)
                            if not chunk:break
                            self.channel.write(chunk)
        except (OSError,ValueError,KeyError,TypeError,web.HTTPException,asyncio.TimeoutError,asyncssh.Error):
            pass
        finally:
            self.channel.close()

    def close(self):
        self.closed=True
        if self.task:self.task.cancel()
        self.channel.close()
