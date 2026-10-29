"""Real HTTP logins and SSH federation. No mocked auth or friend endpoints."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import asyncssh
from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import ec
from tunnel.accounts import canonical,encode
from tunnel.federation import Federation
from tunnel.portal import Portal,password_hash
from tunnel.relay import RelayServer
from tests.test_account_replication import sign,public


class WebAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.password=secrets.token_urlsafe(20);self.clients=[];self.portals=[];self.relays=[];self.feds=[]
        network=secrets.token_urlsafe(48)
        for n in range(2):
            folder=Path(self.temp.name)/str(n);folder.mkdir()
            state=folder/'node-state.json';state.write_text(json.dumps({'admin_hash':password_hash(self.password),'registration_open':True,'public_join':True}))
            key=asyncssh.generate_private_key('ssh-ed25519')
            relay=await RelayServer('127.0.0.1',0,host_key=key,token=secrets.token_urlsafe(48)).start()
            fed=Federation(key,dict(server_host='127.0.0.1',server_port=relay.port,token=relay.token,server_public_key=key.export_public_key().decode()),network_key=network,name='Node '+str(n))
            relay.federation,fed.relay=fed,relay
            portal=Portal(relay,fed,state);fed.publish()
            self.portals.append(portal);self.relays.append(relay);self.feds.append(fed)
        for fed in self.feds:
            fed.ingest([f.records[f.id] for f in self.feds])
            fed.seeds={f.id:f.profile for f in self.feds if f is not fed}
        for portal in self.portals:
            client=TestClient(TestServer(portal.app),cookie_jar=CookieJar(unsafe=True));await client.start_server();self.clients.append(client)
        self.users=[]

    async def asyncTearDown(self):
        for client in self.clients:await client.close()
        await asyncio.gather(*(f.close() for f in self.feds))
        await asyncio.gather(*(r.close() for r in self.relays))

    async def create_user(self,name,home=0):
        store=self.portals[home].account_api.store;key=ec.generate_private_key(ec.SECP256R1());uid=secrets.token_hex(16)
        account=uid+'.'+self.feds[home].id
        state={'account':account,'revision':1,'box':{'salt':encode(secrets.token_bytes(16)),'iv':encode(secrets.token_bytes(12)),'ciphertext':encode(secrets.token_bytes(64)),'iterations':600000}}
        routing={'account':account,'revision':1,'copies':0,'digest':hashlib.sha256(canonical(state)).hexdigest()}
        record=store.enroll(uid,name,public(key),state,sign(key,state),0,sign(key,routing))
        for _ in range(2):
            for fed in self.feds:await fed.sync_once()
        self.users.append((account,key));return account,key

    async def login_user(self,client,user):
        account,key=user;delegate=ec.generate_private_key(ec.SECP256R1())
        challenge=await (await client.post('/api/account/challenge',json={'account':account,'delegation':public(delegate)})).json()
        proof=sign(key,challenge['message'].encode());grant=json.loads(challenge['message'])
        response=await client.post('/api/account/login',json={'nonce':challenge['nonce'],'signature':proof})
        self.assertEqual(response.status,200,await response.text());result=await response.json()
        return {'X-Account-CSRF':result['csrf']},(delegate,grant,proof)

    def event(self,authorization,op,target,**extra):
        key,grant,proof=authorization
        action=dict(op=op,target=target,at=time.time(),id=secrets.token_hex(16),**extra)
        return {'grant':grant,'proof':proof,'action':action,'signature':sign(key,action)}

    async def test_cross_node_search_request_accept_presence_and_remove(self):
        alice=await self.create_user('alice',0);bob=await self.create_user('bob',1)
        a,b=self.clients;ah,aa=await self.login_user(a,alice);bh,ba=await self.login_user(b,bob)
        # Bob is a directory-only account here: searching does not require a vault replica.
        self.assertNotIn(bob[0],self.portals[0].account_api.store.records)
        response=await a.post('/api/account/lookup',json={'username':'BOB'},headers=ah)
        self.assertEqual((await response.json())['users'],[{'account':bob[0],'username':'bob'}])
        response=await a.post('/api/account/social',json={'event':self.event(aa,'request',bob[0])},headers=ah)
        self.assertEqual(response.status,200,await response.text())
        self.assertEqual((await response.json())['friends'],[])
        for fed in self.feds:await fed.sync_once()
        state=await (await b.get('/api/account/social')).json();self.assertTrue(state['requests'][0]['incoming'])
        ref=state['requests'][0]['id']
        response=await b.post('/api/account/social',json={'event':self.event(ba,'accept',alice[0],ref=ref)},headers=bh)
        self.assertEqual(response.status,200,await response.text());self.assertEqual((await response.json())['friends'][0]['account'],alice[0])
        for fed in self.feds:await fed.sync_once()
        for client in (a,b):self.assertEqual(len((await (await client.get('/api/account/social')).json())['friends']),1)
        self.portals[1].account_api.store.social.apply(bob[0],self.event(ba,'presence',bob[0]))
        for fed in self.feds:await fed.sync_once()
        state=await (await a.get('/api/account/social')).json();self.assertTrue(state['friends'][0]['online'])
        response=await a.post('/api/account/social',json={'event':self.event(aa,'remove',bob[0])},headers=ah)
        self.assertEqual(response.status,200)
        for fed in self.feds:await fed.sync_once()
        self.assertEqual((await (await b.get('/api/account/social')).json())['friends'],[])

    async def test_anonymous_csrf_forged_actor_and_unaccepted_friend_are_rejected(self):
        a,b=self.clients
        self.assertEqual((await a.get('/api/account/social')).status,401)
        user=await self.create_user('alice');other=await self.create_user('bob',1)
        headers,authorization=await self.login_user(a,user)
        _,foreign=await self.login_user(b,other)
        self.assertEqual((await a.post('/api/account/social',json={'event':self.event(authorization,'request',other[0])})).status,403)
        self.assertEqual((await a.post('/api/account/social',json={'event':self.event(foreign,'request',user[0])},headers=headers)).status,400)
        self.assertEqual((await a.post('/api/account/social',json={'event':self.event(authorization,'accept',other[0],ref=secrets.token_hex(16))},headers=headers)).status,400)
        self.assertEqual((await a.post('/api/account/social',json={'event':{'action':[]}},headers=headers)).status,400)

    async def test_personal_code_uses_this_session_even_after_another_device_logs_in(self):
        user=await self.create_user('alice');a=self.clients[0]
        headers,authorization=await self.login_user(a,user)
        # A distinct cookie jar signs a different delegation on the same node.
        other=TestClient(a.server,cookie_jar=CookieJar(unsafe=True));await other.start_server()
        try:
            await self.login_user(other,user)
            response=await a.post('/api/profile',json={});self.assertEqual(response.status,200)
            result=await response.json();self.assertEqual(result['identity'],'account')
            claims=json.loads(base64.urlsafe_b64decode(result['profile']['token'].split('.')[1]+'==='))
            self.assertEqual(claims['g']['d'],public(authorization[0]))
        finally:
            # Closing TestClient would also close the shared test server.
            await other.session.close()
        response=await a.get('/connect',allow_redirects=False)
        self.assertEqual(response.status,302);self.assertEqual(response.headers['Location'],'/account/access')

    async def test_device_login_lease_authenticates_across_nodes_and_revocation_propagates(self):
        user=await self.create_user('alice',0);account,identity_key=user
        headers,_=await self.login_user(self.clients[0],user)
        from cryptography.hazmat.primitives.asymmetric import ec
        device_key=ec.generate_private_key(ec.SECP256R1())
        device_public=public(device_key)
        client=self.clients[0]
        started=await client.post('/api/launcher/start',json={'key':device_public,'name':'test-device','remember':True})
        self.assertEqual(started.status,200,await started.text())
        request=await started.json()
        prepared=await client.post('/api/launcher/prepare',json={'id':request['id']},headers=headers)
        self.assertEqual(prepared.status,200,await prepared.text())
        grant=(await prepared.json())['grant']
        approved=await client.post('/api/launcher/approve',json={
            'id':request['id'],'signature':sign(identity_key,canonical(grant))},headers=headers)
        self.assertEqual(approved.status,200,await approved.text())

        message={'p':'buggcraft-launcher-poll-v1','id':request['id'],'nonce':request['nonce']}
        polled=await client.post('/api/launcher/poll',json={
            'id':request['id'],'signature':sign(device_key,canonical(message))})
        self.assertEqual(polled.status,200,await polled.text())
        token=(await polled.json())['profile']['token']
        self.feds[0].publish()
        self.feds[1].ingest([self.feds[0].records[self.feds[0].id]])
        self.assertEqual(self.portals[1].account_api.store.authenticate(token),account)

        revoked=await client.post('/api/launcher/revoke',json={'id':request['id']},headers=headers)
        self.assertEqual(revoked.status,200,await revoked.text())
        self.feds[0].publish()
        self.feds[1].ingest([self.feds[0].records[self.feds[0].id]])
        self.assertIsNone(self.portals[1].account_api.store.authenticate(token))

    async def test_admin_users_include_directory_only_accounts_and_can_block_locally(self):
        user=await self.create_user('elsewhere',1);a=self.clients[0]
        result=await (await a.post('/api/login',json={'username':'admin','password':self.password})).json()
        response=await a.get('/api/admin');users=(await response.json())['accounts'];entry=next(u for u in users if u['account']==user[0])
        self.assertEqual(entry['storage'],'directory');self.assertTrue(entry['directory_synced'])
        self.assertEqual((await a.post('/api/account/block',json={'account':user[0],'blocked':True},headers={'X-CSRF-Token':result['csrf']})).status,200)
        self.assertIn(user[0],self.portals[0].account_api.blocked);self.assertNotIn(user[0],self.portals[1].account_api.blocked)

    async def test_reconnected_authenticated_player_keeps_one_folder_and_separate_sessions(self):
        user=await self.create_user('alice');client=self.clients[0]
        await self.login_user(client,user)
        profile=(await (await client.post('/api/profile',json={})).json())['profile']
        for _ in range(2):
            from tunnel.relay import room_id
            secret=secrets.token_urlsafe(32)
            async with asyncssh.connect(profile['server_host'],profile['server_port'],username='host:'+room_id(secret),
                    password=json.dumps({'token':profile['token'],'secret':secret}),
                    known_hosts=([asyncssh.import_public_key(profile['server_public_key'])],[],[]),
                    client_keys=[],agent_path=None,config=None,x509_trusted_certs=None,x509_trusted_cert_paths=[]):
                await asyncio.sleep(.02)
        await asyncio.sleep(.02);await self.portals[0].diagnostics.flush()
        root=self.portals[0].diagnostics.root
        folders=list(root.glob('account-*'));self.assertEqual(len(folders),1)
        files=list(folders[0].glob('*.jsonl'))
        self.assertEqual(sum('.events.' not in path.name for path in files),2)
        self.assertEqual(sum('.events.' in path.name for path in files),2)
        for path in files:
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows[0]['username'],'alice');self.assertTrue(rows[0]['authenticated'])
            self.assertTrue(any(row['event']=='disconnected' for row in rows))
