"""User-consented launcher attachments over an account-authenticated SSH stream."""
import asyncio
import hashlib
import json
import os
import secrets

import asyncssh
from aiohttp import web

from .feedback import MAX_FILE, MAX_STORAGE, text


class FeedbackUploadSession:
    def __init__(self, peer, channel):
        self.peer, self.channel = peer, channel
        self.reader = asyncio.StreamReader(limit=8192)
        self.task = None
        self.paused = False
        self.closed = False
        self.buffered = 0

    def start(self):
        self.task = asyncio.create_task(self.receive())

    def data_received(self, data, datatype):
        if self.closed or datatype is not None:
            self.close()
            return
        self.buffered += len(data)
        if self.buffered > 2 * 1024**2:
            self.close()
            return
        self.reader.feed_data(data)
        if self.buffered > 256 * 1024:
            self.channel.pause_reading()

    def eof_received(self):
        self.reader.feed_eof()
        return True

    def consume(self, size):
        self.buffered -= size
        if self.buffered < 128 * 1024:
            self.channel.resume_reading()

    def respond(self, value):
        if not self.closed:
            self.channel.write(json.dumps(value, ensure_ascii=True).encode() + b'\n')

    async def receive(self):
        temporary = None
        issue_id = None
        try:
            store = self.peer.relay.federation.feedback
            async with store.uploads, asyncio.timeout(120):
                line = await self.reader.readline()
                self.consume(len(line))
                header = json.loads(line)
                if not isinstance(header, dict) or header.get('consent') is not True:
                    raise ValueError('请先明确同意上传本次诊断日志。')
                account = getattr(self.peer, 'account', None)
                if self.peer.role != 'directory' or not account:
                    raise ValueError('请先使用个人接入码登录节点。')
                size = header.get('size')
                if type(size) is not int or not 0 < size <= MAX_FILE:
                    raise ValueError('诊断包须为 1 字节至 20 MiB。')
                name = text(header.get('name', ''), 120)
                if name != os.path.basename(name) or '/' in name or '\\' in name or not name.endswith('.zip'):
                    raise ValueError('请选择 ZIP 诊断包。')
                metadata = header.get('metadata', {})
                store.validate_data('created', metadata, False)
                actor = {'admin': False, 'account': account,
                         'username': store.fed.accounts.social.name(account)}
                async with store.lock:
                    issues = store.snapshot()
                    if (len(issues) >= 2000 or sum(i['account'] == account and
                            i['status'] != 'resolved' for i in issues) >= 10):
                        raise ValueError('未完成反馈已达上限，请先跟进已有问题。')
                    if sum(p.stat().st_size for p in store.root.glob('*/*.bin')) + size > MAX_STORAGE:
                        raise ValueError('节点日志存储已满。')
                    issue_id = secrets.token_hex(16)
                    store.add(issue_id, 'created', metadata, actor)
                directory = store.root / issue_id
                directory.mkdir(mode=0o700, exist_ok=True)
                temporary = directory / (secrets.token_hex(16) + '.part')
                digest = hashlib.sha256()
                received = 0
                self.respond({'ok': True, 'ready': True, 'issue': issue_id})
                with temporary.open('xb') as output:
                    temporary.chmod(0o600)
                    while received < size:
                        chunk = await self.reader.read(min(65536, size - received))
                        if not chunk:
                            raise ValueError('上传中断，反馈说明已保存，请补传日志。')
                        self.consume(len(chunk))
                        received += len(chunk)
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
                    extra = await self.reader.read(1)
                    if extra:
                        raise ValueError('诊断包大小与声明不符。')
                    output.flush()
                    os.fsync(output.fileno())
                async with store.lock:
                    if store.issue(issue_id)['status'] == 'resolved':
                        raise ValueError('问题已完成，附件不再保存。')
                    if sum(p.stat().st_size for p in store.root.glob('*/*.bin')) + size > MAX_STORAGE:
                        raise ValueError('节点日志存储已满。')
                    event = store.add(issue_id, 'attachment', {
                        'name': name, 'size': received, 'sha256': digest.hexdigest()}, actor)
                    os.replace(temporary, directory / (event['id'] + '.bin'))
                self.respond({'ok': True, 'issue': issue_id, 'attachment': event['id'],
                              'size': received, 'sha256': digest.hexdigest()})
        except (ValueError, KeyError, TypeError, OSError, web.HTTPException,
                asyncssh.Error, asyncio.TimeoutError) as exc:
            self.respond({'ok': False, 'issue': issue_id,
                          'error': str(exc)[:160] if isinstance(exc, ValueError) else '诊断包接收失败，请稍后补传。'})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.channel.close()

    def close(self):
        self.closed = True
        self.reader.feed_eof()
        if self.task:
            self.task.cancel()
        self.channel.close()
