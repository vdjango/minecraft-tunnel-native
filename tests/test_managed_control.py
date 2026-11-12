"""Managed relay authorization boundary: no alternate account or directory login."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import asyncssh
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests import test_native as native_fixture
from tests import test_profile_exchange as http_fixture
from tunnel.portal import Portal
from tunnel.managed_control import ManagedControl


class AllocationRenewalTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, count=600):
        control = ManagedControl.__new__(ManagedControl)
        control.allocations = {f'{i:05}': dict(ticket='private-test-ticket', disconnected=None) for i in range(count)}
        control.lock = asyncio.Lock()
        control.renew_cursor = ''
        control.batch_refresh = True
        control.saved = dict(token='private-test-grant', expires_at=time.time()+3600)
        control.relay = SimpleNamespace(peers=[])
        control.status, control.last_error, control.lease_error = 'ready', None, None
        control.sequence = 0
        control.session = control.task = control.renew_task = None
        return control

    async def test_more_than_256_allocations_are_refreshed_without_ticket_fanout(self):
        control, covered, requests = self.adapter(), set(), []
        async def call(path, body):
            self.assertEqual(path, 'placement/refresh')
            self.assertLessEqual(len(body['reservations']), 256)
            self.assertNotIn('ticket', str(body))
            requests.append(body)
            covered.update(row['reservation_id'] for row in body['reservations'])
            return dict(reservations=[dict(row, state='active') for row in body['reservations']])
        control.call = call
        for _ in range(3):
            await control.renew_allocations()
        self.assertEqual(covered, set(control.allocations))
        self.assertEqual(len(requests), 3)

    async def test_failed_prefix_rotates_and_expired_entries_are_removed(self):
        control = self.adapter()
        async def failed(path, body):
            raise TimeoutError()
        control.call = failed
        with self.assertRaises(TimeoutError):
            await control.renew_allocations()
        selected = []
        async def call(path, body):
            selected.extend(row['reservation_id'] for row in body['reservations'])
            return dict(reservations=[dict(reservation_id=row['reservation_id'], error='expired') for row in body['reservations']])
        control.call = call
        await control.renew_allocations()
        self.assertEqual(selected[0], '00256')
        self.assertEqual(len(control.allocations), 600-256)

    async def test_blocked_refresh_does_not_block_heartbeat_and_close_cancels_both(self):
        control = self.adapter(1)
        refreshing, heartbeat = asyncio.Event(), asyncio.Event()
        async def call(path, body):
            if path == 'placement/refresh':
                refreshing.set()
                await asyncio.Future()
            elif path == 'node/heartbeat':
                heartbeat.set()
                return {}
            self.fail('Unexpected operation')
        control.call = call
        control.renew_task = asyncio.create_task(control.renew_loop())
        await asyncio.wait_for(refreshing.wait(), 1)
        control.task = asyncio.create_task(control.run())
        await asyncio.wait_for(heartbeat.wait(), 1)
        tasks = [control.task, control.renew_task]
        await control.close()
        self.assertTrue(all(task.done() for task in tasks))
        self.assertIsNone(control.task)
        self.assertIsNone(control.renew_task)


class ManagedLoginTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_fixture.NativeTests.asyncSetUp
    asyncTearDown = native_fixture.NativeTests.asyncTearDown
    connect = native_fixture.NativeTests.connect

    async def test_shared_key_and_old_account_cannot_login_managed_relay(self):
        legacy = Mock(side_effect=AssertionError('Legacy authentication must never run'))
        self.relay.account_authenticate = legacy
        managed = SimpleNamespace(player=Mock(side_effect=ValueError('Invalid managed authorization')),
                                  disconnected=Mock())
        self.relay.managed_control = managed
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.connect()
        managed.player.assert_not_called()
        self.relay.token = 'acct1.old-node-token'
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.connect('host', 's' * 43)
        managed.player.assert_called_once_with('acct1.old-node-token')
        legacy.assert_not_called()

    # HTTP removal is checked on the actual headless runtime in test_service_portal.
