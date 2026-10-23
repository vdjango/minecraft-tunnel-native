"""Issue permissions, signed federation, real file transfer and deletion."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from aiohttp import FormData
from aiohttp import web
from tests import test_web_accounts as fixtures


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.WebAccountTests.asyncSetUp
    asyncTearDown=fixtures.WebAccountTests.asyncTearDown
    create_user=fixtures.WebAccountTests.create_user
    login_user=fixtures.WebAccountTests.login_user

    async def setup_report(self):
        self.user=await self.create_user('reporter')
        self.headers,_=await self.login_user(self.clients[0],self.user)
        response=await self.clients[0].post('/api/feedback',json={'title':'Respawn stalls','description':'After dying, only the respawning player freezes.','game_version':'1.21.1','loader':'Fabric','launcher_version':'0.1','system':'Windows 10'},headers=self.headers)
        self.assertEqual(response.status,201,await response.text())
        self.issue=(await response.json())['id'];return self.issue

    async def upload(self,content=b'private-game-log',name='game.log'):
        data=FormData();data.add_field('file',io.BytesIO(content),filename=name,content_type='application/octet-stream')
        return await self.clients[0].post('/api/feedback/'+self.issue+'/attachments',data=data,headers=self.headers)

    async def sync_feedback(self):
        for portal in self.portals:
            async with asyncio.timeout(10):
                while portal.feedback.sync_lock.locked():await asyncio.sleep(.02)
            await portal.feedback.sync_once()

    async def admin(self,index):
        result=await (await self.clients[index].post('/api/login',json={'username':'admin','password':self.password})).json()
        return {'X-CSRF-Token':result['csrf'],'X-Feedback-Role':'admin'}

    async def test_cross_node_view_private_transfer_audit_and_resolved_file_deletion(self):
        await self.setup_report();response=await self.upload();self.assertEqual(response.status,201,await response.text());attachment=(await response.json())['id']
        await self.sync_feedback()
        headers=await self.admin(1)
        detail=await (await self.clients[1].get('/api/feedback/'+self.issue)).json()
        self.assertEqual(detail['issue']['title'],'Respawn stalls')
        self.assertEqual(len(detail['issue']['attachments']),1)
        download=await self.clients[1].get(f'/api/feedback/{self.issue}/attachments/{attachment}')
        self.assertEqual(download.status,200,await download.text());self.assertEqual(await download.read(),b'private-game-log')
        self.assertIn('attachment;',download.headers['Content-Disposition'])
        for n in (0,1):self.assertTrue((self.portals[n].feedback.root/self.issue/(attachment+'.bin')).exists())
        response=await self.clients[1].post('/api/feedback/'+self.issue+'/status',json={'status':'accepted','message':'Assigned to operator; reproducing.'},headers=headers)
        self.assertEqual(response.status,200,await response.text())
        response=await self.clients[1].post('/api/feedback/'+self.issue+'/status',json={'status':'resolved','message':'Fixed and verified in 0.2.'},headers=headers)
        self.assertEqual(response.status,200);self.assertTrue((await response.json())['attachments_deleted'])
        await self.sync_feedback()
        for portal in self.portals:
            issue=portal.feedback.issue(self.issue)
            self.assertEqual(issue['status'],'resolved')
            self.assertEqual([e['data']['message'] for e in issue['history'] if e['kind']=='status'],['Assigned to operator; reproducing.','Fixed and verified in 0.2.'])
            self.assertFalse(list((portal.feedback.root/self.issue).glob('*.bin')))
        self.assertEqual((await self.clients[1].get(f'/api/feedback/{self.issue}/attachments/{attachment}')).status,410)
        # Resolution is a durable tombstone: a replayed old attachment cannot restore files.
        self.assertEqual((await self.upload()).status,410)
        self.assertEqual((await self.clients[1].post('/api/feedback/'+self.issue+'/status',json={'status':'investigating','message':'Reopen'},headers=headers)).status,409)

    async def test_all_users_see_issue_but_not_others_files_and_cannot_close_it(self):
        await self.setup_report();response=await self.upload();attachment=(await response.json())['id']
        await self.sync_feedback()
        other=await self.create_user('reader',1);headers,_=await self.login_user(self.clients[1],other)
        response=await self.clients[1].get('/api/feedback');self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['issues'][0]['id'],self.issue)
        response=await self.clients[1].get('/api/feedback/'+self.issue);detail=await response.json()
        self.assertEqual(detail['issue']['description'],'After dying, only the respawning player freezes.')
        self.assertEqual(detail['issue']['attachments'],[])
        self.assertFalse(any(e['kind']=='attachment' for e in detail['issue']['history']))
        self.assertEqual((await self.clients[1].get(f'/api/feedback/{self.issue}/attachments/{attachment}')).status,401)
        self.assertEqual((await self.clients[1].post('/api/feedback/'+self.issue+'/status',json={'status':'resolved','message':'Fake fix'},headers=headers)).status,401)
        response=await self.clients[1].post('/api/feedback/'+self.issue+'/comments',json={'message':'I can reproduce this too.'},headers=headers)
        self.assertEqual(response.status,200)
        self.assertEqual((await self.clients[1].post('/api/feedback/'+self.issue+'/comments',json={'message':'CSRF'})).status,403)

    async def test_upload_validation_anonymous_access_and_tampered_gossip(self):
        self.assertEqual((await self.clients[0].get('/api/feedback')).status,401)
        await self.setup_report()
        self.assertEqual((await self.upload(b'bad','payload.exe')).status,400)
        self.assertEqual((await self.upload(b'','empty.log')).status,400)
        self.assertEqual((await self.upload(b'x'*(20*1024**2+1))).status,413)
        self.assertFalse(list(self.portals[0].feedback.root.glob('*/*.part')))
        event=next(iter(self.portals[0].feedback.events.values()));tampered=copy.deepcopy(event);tampered['body']['data']['description']='Changed by attacker'
        with self.assertRaises(ValueError):self.portals[1].feedback.ingest(tampered)
        # Membership in another network cannot export previous network feedback.
        old=self.feds[0].network_key;self.feds[0].network_key='other-network-secret'*3
        try:
            self.assertEqual(self.portals[0].feedback.rpc({'op':'feedback_index'})['ids'],[])
            self.assertEqual(self.portals[0].feedback.snapshot(),[])
        finally:self.feds[0].network_key=old

    async def test_issue_and_resolution_survive_restart_and_late_replica_cleanup(self):
        await self.setup_report();response=await self.upload();attachment=(await response.json())['id']
        await self.sync_feedback();headers=await self.admin(0)
        response=await self.clients[0].post('/api/feedback/'+self.issue+'/status',json={'status':'resolved','message':'No longer reproducible, verified.'},headers=headers)
        self.assertEqual(response.status,200,await response.text())
        await self.sync_feedback()
        from tunnel.feedback import Feedback
        reopened=Feedback(self.portals[1]);issue=reopened.issue(self.issue)
        self.assertEqual(issue['status'],'resolved');self.assertTrue(issue['attachments'][0]['deleted'])
        self.assertFalse((reopened.root/self.issue/(attachment+'.bin')).exists())

    async def test_corrupt_remote_attachment_is_refused_and_partial_transfer_is_removed(self):
        await self.setup_report();response=await self.upload();attachment=(await response.json())['id']
        await self.sync_feedback()
        source=self.portals[0].feedback.root/self.issue/(attachment+'.bin')
        source.write_bytes(b'corrupted-contents')
        target=self.portals[1].feedback.root/self.issue/(attachment+'.bin');target.unlink(missing_ok=True)
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.portals[1].feedback.ensure_file(self.issue,attachment)
        self.assertFalse(target.exists());self.assertFalse(list(target.parent.glob('*.part-*')))

    async def test_resolution_rejects_downloads_even_if_file_deletion_needs_retry(self):
        await self.setup_report();response=await self.upload();attachment=(await response.json())['id']
        headers=await self.admin(0);store=self.portals[0].feedback
        original=Path.unlink
        def locked(path,*args,**kwargs):
            if path.suffix=='.bin':raise PermissionError('file temporarily locked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',locked):
            response=await self.clients[0].post('/api/feedback/'+self.issue+'/status',json={'status':'resolved','message':'Fixed and validated.'},headers=headers)
            self.assertEqual(response.status,200);self.assertFalse((await response.json())['attachments_deleted'])
            self.assertIn(self.issue,store.cleanup_pending)
            self.assertEqual((await self.clients[0].get(f'/api/feedback/{self.issue}/attachments/{attachment}')).status,410)
        store.cleanup(self.issue)
        self.assertNotIn(self.issue,store.cleanup_pending)
        self.assertFalse((store.root/self.issue/(attachment+'.bin')).exists())
