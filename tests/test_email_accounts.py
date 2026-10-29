"""Mailbox ownership, partitioned registration and portable account IDs over HTTP/SSH."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import asyncssh
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from tunnel.accounts import Accounts, canonical, encode
from tunnel.federation import Federation
from tunnel.portal import Portal
from tunnel.relay import RelayServer


def sign(key, raw):
    r, s = utils.decode_dss_signature(key.sign(raw, ec.ECDSA(hashes.SHA256())))
    return encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))


def public(key):
    p = key.public_key().public_numbers()
    return dict(kty='EC', crv='P-256', x=encode(p.x.to_bytes(32, 'big')), y=encode(p.y.to_bytes(32, 'big')))


class EmailAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feds, self.portals, self.clients, self.relays, self.messages = [], [], [], [], []
        network = secrets.token_urlsafe(48)
        for n in range(2):
            folder = Path(self.temp.name) / str(n)
            folder.mkdir()
            path = folder / 'node-state.json'
            path.write_text(json.dumps({'registration_open': True}), encoding='utf-8')
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48)).start()
            profile = dict(server_host='127.0.0.1', server_port=relay.port, token=relay.token,
                           server_public_key=key.export_public_key().decode())
            fed = Federation(key, profile, network_key=network, name='中国节点' + str(n))
            relay.federation, fed.relay = fed, relay
            portal = Portal(relay, fed, path)
            fed.publish()
            portal.account_api.mail.configure(dict(username='sender@163.com', password='local-test', required=True))
            portal.account_api.mail._send = self.messages.append
            # Rate limiting has separate coverage; permit multiple simulated users.
            portal.throttle = lambda request: None
            client = TestClient(TestServer(portal.app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            self.feds.append(fed); self.portals.append(portal); self.clients.append(client); self.relays.append(relay)
        for fed in self.feds:
            fed.seeds = {f.id: f.profile for f in self.feds if f is not fed}

    async def asyncTearDown(self):
        await asyncio.gather(*(c.close() for c in self.clients))
        await asyncio.gather(*(f.close() for f in self.feds))
        await asyncio.gather(*(r.close() for r in self.relays))

    async def code(self, n=0, email='Owner@QQ.com', purpose='login', **extra):
        self.portals[n].account_api.mail.recent.clear()
        with patch('tunnel.mail.secrets.randbelow', return_value=123456):
            response = await self.clients[n].post('/api/account/email-code', json=dict(email=email, purpose=purpose, **extra))
        self.assertEqual(response.status, 200, await response.text())
        result = await response.json()
        return dict(ticket=result['ticket'], nonce=result['nonce'], code='123456')

    async def register(self, n=0, email='Owner@QQ.com', name='same_user'):
        client = self.clients[n]
        enrollment = await (await client.post('/api/account/enroll', json={})).json()
        code = await self.code(n, email, 'register', username=name, nonce=enrollment['nonce'])
        key = ec.generate_private_key(ec.SECP256R1())
        state = dict(account=enrollment['account'], revision=1, box=dict(salt=encode(secrets.token_bytes(16)),
                     iv=encode(secrets.token_bytes(12)), ciphertext=encode(secrets.token_bytes(64)), iterations=600000))
        route = dict(account=state['account'], revision=1, copies=1, digest=hashlib.sha256(canonical(state)).hexdigest())
        response = await client.post('/api/account/register', json=dict(nonce=enrollment['nonce'], username=name,
            email_ticket=code['ticket'], email_code=code['code'], public_key=public(key), state=state,
            signature=sign(key, canonical(state)), copies=1, routing_signature=sign(key, canonical(route)),
            proof=sign(key, enrollment['message'].encode())))
        self.assertEqual(response.status, 201, await response.text())
        return state['account'], key

    async def verified(self, n=0, email='owner@qq.com'):
        code = await self.code(n, email)
        response = await self.clients[n].post('/api/account/email-challenge', json=code)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def login(self, n, account, key, grant=None):
        response = await self.clients[n].post('/api/account/challenge', json=dict(account=account))
        self.assertEqual(response.status, 200, await response.text())
        challenge = await response.json()
        return await self.clients[n].post('/api/account/login', json=dict(nonce=challenge['nonce'],
            signature=sign(key, challenge['message'].encode()), **({'email_grant': grant} if grant else {})))

    async def test_partition_registration_same_name_and_mailbox_converges_and_choice_survives_restart(self):
        with patch.object(self.feds[0], 'reserve_username', AsyncMock(side_effect=AssertionError('global reservation'))), \
                patch.object(self.feds[0], 'sync_once', AsyncMock(side_effect=AssertionError('network wait'))):
            first, first_key = await asyncio.wait_for(self.register(0), 2)
        second, second_key = await self.register(1, email='owner@qq.com')
        self.assertNotEqual(first, second)
        self.assertEqual(len((await self.verified(0))['accounts']), 1)
        for fed in self.feds:
            await fed.sync_once()
        result = await self.verified(0)
        self.assertEqual({a['account'] for a in result['accounts']}, {first, second})
        self.assertFalse(any(a['preferred'] for a in result['accounts']))
        response = await self.login(0, second, second_key, result['grant'])
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())['account'], second)
        for fed in self.feds:
            await fed.sync_once()
        result = await self.verified(1)
        self.assertEqual(result['accounts'][0]['account'], second)
        self.assertTrue(result['accounts'][0]['preferred'])
        store = self.portals[1].account_api.store
        restored = Accounts(self.feds[1], store.path)
        self.assertEqual(restored.email_accounts.candidates(restored.email_accounts.mailbox('OWNER@QQ.COM'))[0]['account'], second)
        # Both original signed identities and keys still work; no silent merge.
        self.assertEqual((await self.login(0, first, first_key)).status, 200)

    async def test_otp_is_required_single_use_and_purpose_bound(self):
        await self.register()
        for payload in ({}, dict(ticket='invalid', nonce='x', code='123456')):
            self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=payload)).status, 400)
        code = await self.code()
        bad = dict(code, code='654321')
        self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=bad)).status, 400)
        self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=code)).status, 400)
        enrollment = await (await self.clients[0].post('/api/account/enroll', json={})).json()
        register_code = await self.code(purpose='register', username='same_user', nonce=enrollment['nonce'])
        self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=register_code)).status, 400)
        code = await self.code()
        self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=code)).status, 200)
        self.assertEqual((await self.clients[0].post('/api/account/email-challenge', json=code)).status, 400)

    async def test_mailbox_grant_cannot_select_unrelated_account_or_replay(self):
        first, key = await self.register()
        other, other_key = await self.register(email='different@163.com')
        verified = await self.verified()
        self.assertEqual((await self.login(0, other, other_key, verified['grant'])).status, 400)
        verified = await self.verified()
        self.assertEqual((await self.login(0, first, key, verified['grant'])).status, 200)
        self.assertEqual((await self.login(0, first, key, verified['grant'])).status, 400)

    async def test_legacy_binding_needs_session_csrf_and_mail_proof(self):
        self.assertEqual((await self.clients[0].post('/api/account/email-code', json=dict(email='old@163.com', purpose='link'))).status, 401)
        account, key = await self.register(email='old@qq.com')
        response = await self.login(0, account, key)
        headers = {'X-Account-CSRF': (await response.json())['csrf']}
        data = dict(email='legacy@163.com', purpose='link')
        self.assertEqual((await self.clients[0].post('/api/account/email-code', json=data)).status, 403)
        with patch('tunnel.mail.secrets.randbelow', return_value=123456):
            response = await self.clients[0].post('/api/account/email-code', json=data, headers=headers)
        self.assertEqual(response.status, 200, await response.text())
        ticket = (await response.json())['ticket']
        response = await self.clients[0].post('/api/account/email-accounts', json=dict(ticket=ticket, code='123456'), headers=headers)
        self.assertEqual(response.status, 200, await response.text())
        result = await self.verified(email='legacy@163.com')
        self.assertEqual([a['account'] for a in result['accounts']], [account])
        self.assertTrue(result['accounts'][0]['preferred'])

    async def test_closed_registration_does_not_disable_mailbox_login_and_empty_mailboxes_are_private(self):
        account, key = await self.register()
        self.portals[0].state['registration_open'] = False
        verified = await self.verified()
        self.assertEqual((await self.login(0, account, key, verified['grant'])).status, 200)
        self.assertEqual((await self.verified(email='unknown@163.com'))['accounts'], [])
        response = await self.clients[0].post('/api/account/enroll', json={})
        self.assertEqual(response.status, 403)
        public_json = json.dumps(self.portals[0].account_api.store.replication.snapshot())
        self.assertNotIn('mailbox', public_json)
        self.assertNotIn('qq.com', public_json.lower())

    async def test_tampering_blocked_nodes_and_network_changes_cannot_reuse_mail_links(self):
        await self.register()
        source = self.portals[0].account_api.store.email_accounts
        target = self.portals[1].account_api.store.email_accounts
        event = copy.deepcopy(source.snapshot()[0])
        event['body']['mailbox'] = 'f' * 64
        target.ingest([event])
        self.assertFalse(target.entries)
        target.ingest(source.snapshot())
        self.assertEqual(len(target.entries), 1)
        self.feds[1].blocked.add(self.feds[0].id)
        self.assertEqual(target.snapshot(), [])
        self.assertEqual(target.candidates(target.mailbox('owner@qq.com')), [])
        self.feds[1].blocked.clear()
        self.feds[1].network_key = secrets.token_urlsafe(48)
        self.assertEqual(target.snapshot(), [])

    async def test_chinese_mail_content_and_smtp_failure_allows_retry(self):
        api = self.portals[0].account_api
        with patch.object(api.mail, '_send', side_effect=TimeoutError('private detail')):
            response = await self.clients[0].post('/api/account/email-code', json=dict(email='owner@qq.com', purpose='login'))
        self.assertEqual(response.status, 503)
        self.assertNotIn('private detail', await response.text())
        self.assertFalse(api.mail.pending)
        self.assertFalse(api.mail.recent)
        code = await self.code()
        self.assertTrue(code['ticket'])
        message = self.messages[-1]
        self.assertIn('邮箱验证码', str(message['Subject']))
        self.assertIn('中国节点0', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('123456', message.get_body(preferencelist=('html',)).get_content())
        response = await self.clients[0].post('/api/account/email-code', json=dict(email='OWNER@QQ.COM', purpose='login'))
        self.assertEqual(response.status, 400)
