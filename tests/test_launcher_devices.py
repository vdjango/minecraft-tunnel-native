import unittest

from cryptography.hazmat.primitives.asymmetric import ec

from test_email_accounts import canonical, public, sign


class LauncherDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_email_accounts import EmailAccountTests
        self.fixture = EmailAccountTests('runTest')
        await self.fixture.asyncSetUp()
        for portal in self.fixture.portals:
            portal.throttle = lambda request, **kwargs: None

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_browser_approved_device_refresh_and_revocation(self):
        account, identity_key = await self.fixture.register()
        login = await self.fixture.login(0, account, identity_key)
        self.assertEqual(login.status, 200, await login.text())
        csrf = (await login.json())['csrf']
        client = self.fixture.clients[0]
        store = self.fixture.portals[0].account_api.store
        self.assertEqual(store.authenticate(store.credential(account)), account)
        device_key = ec.generate_private_key(ec.SECP256R1())
        key = public(device_key)

        started = await client.post('/api/launcher/start', json={
            'key': key, 'name': 'test-device', 'remember': True})
        self.assertEqual(started.status, 200, await started.text())
        request = await started.json()
        info = await client.get('/api/launcher/info', params={'id': request['id']})
        self.assertEqual(info.status, 200, await info.text())

        headers = {'X-Account-CSRF': csrf}
        prepared = await client.post('/api/launcher/prepare', json={'id': request['id']}, headers=headers)
        self.assertEqual(prepared.status, 200, await prepared.text())
        grant = (await prepared.json())['grant']
        approved = await client.post('/api/launcher/approve', json={
            'id': request['id'], 'signature': sign(identity_key, canonical(grant))}, headers=headers)
        self.assertEqual(approved.status, 200, await approved.text())

        poll_message = {'p': 'buggcraft-launcher-poll-v1', 'id': request['id'], 'nonce': request['nonce']}
        polled = await client.post('/api/launcher/poll', json={
            'id': request['id'], 'signature': sign(device_key, canonical(poll_message))})
        self.assertEqual(polled.status, 200, await polled.text())
        credential = (await polled.json())['profile']['token']
        self.assertEqual(store.authenticate(credential), account)

        challenge = await client.post('/api/launcher/challenge', json={'id': request['id']})
        self.assertEqual(challenge.status, 200, await challenge.text())
        message = (await challenge.json())['message']
        refreshed = await client.post('/api/launcher/refresh', json={
            'nonce': message['nonce'], 'signature': sign(device_key, canonical(message))})
        self.assertEqual(refreshed.status, 200, await refreshed.text())
        self.assertEqual(store.authenticate((await refreshed.json())['profile']['token']), account)

        revoked = await client.post('/api/launcher/revoke', json={'id': request['id']}, headers=headers)
        self.assertEqual(revoked.status, 200, await revoked.text())
        self.assertIsNone(store.authenticate(credential))


if __name__ == '__main__':
    unittest.main()
