"""Modern role boundaries, local account isolation and removed public endpoints."""
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from tests.service_fixture import ServiceFixture
from tests.account_fixture import enroll, sign


class ServicePortalTests(ServiceFixture, unittest.IsolatedAsyncioTestCase):
    async def test_local_accounts_do_not_create_replication_or_social(self):
        store = self.authority.account_api.store
        self.assertFalse(hasattr(store, 'replication'))
        self.assertFalse(hasattr(store, 'social'))
        self.assertFalse(hasattr(self.fed, 'task'))
        self.assertFalse(hasattr(self.fed, 'transport'))
        account, _, key = enroll(store, 'central_user', return_key=True)
        self.fed.lookup_accounts = AsyncMock(side_effect=AssertionError('Must not contact a node'))
        challenge = await self.client.post('/api/account/challenge', json={'account':'CENTRAL_USER'})
        self.assertEqual(challenge.status, 200, await challenge.text())
        value = await challenge.json()
        import json
        response = await self.client.post('/api/account/login', json={
            'nonce':value['nonce'], 'signature':sign(key, json.loads(value['message']))})
        self.assertEqual(response.status, 200, await response.text())
        me = await (await self.client.get('/api/account/me')).json()
        self.assertEqual(me['account'], account)
        self.assertNotIn('backup', me)
        self.fed.lookup_accounts.assert_not_called()

    async def test_control_only_exposes_core_account_and_device_routes(self):
        for path in ('/admin','/network','/feedback','/developers','/diagnostics',
                     '/account/security','/account/profile','/account/friends',
                     '/api/account/backup','/api/account/recover','/api/account/record/a',
                     '/api/account/social','/api/account/lookup','/api/admin/mail',
                     '/api/login','/api/control/invitations','/vue-page-admin.js'):
            for method in ('get','post'):
                response = await getattr(self.client, method)(path, **({'json':{}} if method=='post' else {}))
                self.assertEqual(response.status,404,(method,path,await response.text()))
                self.assertIn('nosniff',response.headers.get('X-Content-Type-Options',''))

    async def test_headless_node_has_no_account_admin_or_federation(self):
        node = self.node_surface()
        node.control.run = AsyncMock()
        self.assertIsNone(self.relay.federation)
        self.assertFalse(hasattr(node,'account_api'))
        self.assertFalse(hasattr(node,'feedback'))
        async with TestClient(TestServer(node.app)) as client:
            for path in ('/','/admin','/login','/register','/account','/network',
                         '/api/account/login','/api/launcher/start','/api/login',
                         '/api/profile/redeem','/vue-page-account.js'):
                for method in ('get','post'):
                    response=await getattr(client,method)(path,**({'json':{}} if method=='post' else {}))
                    self.assertEqual(response.status,404,(method,path))
            status=await (await client.get('/api/status')).json()
            self.assertEqual(status['launcher_login'],0)
            self.assertEqual(status['role'],'relay')
        self.assertIsNone(node.control.task)
        self.assertIsNone(node.control.session)

    async def test_origin_and_csrf_still_protect_core_routes(self):
        response=await self.client.post('/api/account/enroll',json={},headers={'Origin':'https://other.example'})
        self.assertEqual(response.status,403)
        response=await self.client.post('/api/account/enroll',data='{}',headers={'Content-Type':'text/plain'})
        self.assertEqual(response.status,415)
