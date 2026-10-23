"""Cache hits require no network; misses cannot turn player input into a mesh storm."""
import asyncio
import copy
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from tunnel.accounts import Accounts
from tunnel.account_replication import header
from tunnel.federation import Federation
from tunnel.federation_lookup import PROBE_FANOUT
from tests.account_fixture import enroll


class LookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.feds = []
        network = secrets.token_urlsafe(48)
        for index in range(64):
            key = asyncssh.generate_private_key('ssh-ed25519')
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=3333 + index,
                token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
                network_key=network)
            fed.relay = SimpleNamespace(rooms={}, account_blocked=lambda a: False)
            folder = Path(temporary.name) / str(index)
            folder.mkdir()
            fed.accounts = Accounts(fed, folder / 'accounts.json')
            fed.publish()
            self.feds.append(fed)
            self.addAsyncCleanup(fed.close)
        self.node, self.owner = self.feds[:2]
        self.account, _ = enroll(self.owner.accounts, 'remote_friend')
        self.entry = header(self.owner.accounts.records[self.account])
        self.node.ingest([f.records[f.id] for f in self.feds[1:]])

    async def test_local_match_does_not_contact_sixty_three_peers(self):
        self.node.accounts.replication.accept(self.entry)
        with patch.object(self.node, 'account_rpc', new=AsyncMock()) as rpc:
            self.assertEqual(await self.node.lookup_accounts('REMOTE_FRIEND'),
                             [dict(account=self.account, username='remote_friend')])
            rpc.assert_not_awaited()

    async def test_full_account_miss_queries_home_directly_and_repairs_public_cache(self):
        async def rpc(profile, op, **payload):
            return dict(entries=[self.entry] if profile == self.owner.profile else [])
        with patch.object(self.node, 'account_rpc', new=AsyncMock(side_effect=rpc)) as calls:
            found = await self.node.lookup_accounts(self.account)
            self.assertEqual(found, [dict(account=self.account, username='remote_friend')])
            self.assertEqual(calls.await_count, PROBE_FANOUT)
            self.assertEqual(calls.await_args_list[0].args[0], self.owner.profile)
            self.assertEqual(await self.node.lookup_accounts('REMOTE_FRIEND'), found)
            self.assertEqual(calls.await_count, PROBE_FANOUT)
        self.assertNotIn(self.account, self.node.accounts.records, 'A public lookup must not replicate an encrypted vault')
        self.assertNotIn('ciphertext', self.node.accounts.replication.path.read_text(encoding='utf-8'))

    async def test_same_miss_is_coalesced_and_caller_cancellation_does_not_cancel_repair(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def rpc(*args, **kwargs):
            entered.set()
            await release.wait()
            return dict(entries=[self.entry])
        with patch.object(self.node, 'account_rpc', new=AsyncMock(side_effect=rpc)) as calls:
            first = asyncio.create_task(self.node.lookup_accounts('remote_friend'))
            await entered.wait()
            others = [asyncio.create_task(self.node.lookup_accounts('REMOTE_FRIEND')) for _ in range(40)]
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            release.set()
            results = await asyncio.gather(*others)
            self.assertTrue(all(r == [dict(account=self.account, username='remote_friend')] for r in results))
            self.assertEqual(calls.await_count, PROBE_FANOUT)
        self.assertFalse(self.node.account_lookup.pending)

    async def test_many_distinct_misses_and_negative_repeats_have_fixed_node_budget(self):
        async def rpc(*args, **kwargs):
            await asyncio.sleep(.01)
            return dict(entries=[])
        with patch.object(self.node, 'account_rpc', new=AsyncMock(side_effect=rpc)) as calls:
            await asyncio.gather(*(self.node.lookup_accounts('unknown_' + str(i)) for i in range(200)))
            self.assertLessEqual(calls.await_count, PROBE_FANOUT)
            await self.node.lookup_accounts('unknown_0')
            self.assertLessEqual(calls.await_count, PROBE_FANOUT)
        self.assertFalse(self.node.account_lookup.pending)

    async def test_gossip_result_bypasses_negative_cache_and_keeps_duplicate_names(self):
        self.node.account_lookup.negative['remote_friend'] = float('inf')
        self.node.accounts.replication.accept(self.entry)
        other = self.feds[2]
        duplicate, _ = enroll(other.accounts, 'remote_friend')
        self.node.accounts.replication.accept(header(other.accounts.records[duplicate]))
        with patch.object(self.node, 'account_rpc', new=AsyncMock()) as calls:
            found = await self.node.lookup_accounts('remote_friend')
            self.assertEqual({r['account'] for r in found}, {self.account, duplicate})
            calls.assert_not_awaited()

    async def test_forged_or_unknown_home_headers_are_not_cached(self):
        forged = copy.deepcopy(self.entry)
        forged['certificate']['username'] = 'forged_friend'
        with patch.object(self.node, 'account_rpc', new=AsyncMock(return_value=dict(entries=[forged]))):
            self.assertEqual(await self.node.lookup_accounts('forged_friend'), [])
        self.assertNotIn(self.account, self.node.accounts.replication.directory)
        self.node.account_lookup.reset()
        self.node.records.pop(self.owner.id)
        with patch.object(self.node, 'account_rpc', new=AsyncMock(return_value=dict(entries=[self.entry]))):
            self.assertEqual(await self.node.lookup_accounts(self.account), [])
        self.assertNotIn(self.account, self.node.accounts.replication.directory)

    async def test_blocked_home_and_player_stay_hidden(self):
        self.node.accounts.replication.accept(self.entry)
        self.node.relay.account_blocked = lambda a: a == self.account
        with patch.object(self.node, 'account_rpc', new=AsyncMock(return_value=dict(entries=[self.entry]))):
            self.assertEqual(await self.node.lookup_accounts(self.account), [])
        self.node.relay.account_blocked = lambda a: False
        self.node.account_lookup.reset()
        self.node.blocked.add(self.owner.id)
        with patch.object(self.node, 'account_rpc', new=AsyncMock(return_value=dict(entries=[self.entry]))):
            self.assertEqual(await self.node.lookup_accounts(self.account), [])

    async def test_failed_probe_does_not_break_local_queries_or_leak_tasks(self):
        with patch.object(self.node, 'account_rpc', new=AsyncMock(side_effect=OSError('unreachable'))):
            self.assertEqual(await self.node.lookup_accounts(self.account), [])
        self.assertFalse(self.node.account_lookup.pending)
        self.node.accounts.replication.accept(self.entry)
        self.assertEqual(len(await self.node.lookup_accounts(self.account)), 1)

    async def test_leave_cancels_old_network_probe(self):
        entered = asyncio.Event()
        async def rpc(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.node, 'account_rpc', new=AsyncMock(side_effect=rpc)):
            task = asyncio.create_task(self.node.lookup_accounts('remote_friend'))
            await entered.wait()
            self.node.leave()
            result = await asyncio.gather(task, return_exceptions=True)
            self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertFalse(self.node.account_lookup.pending)


if __name__ == '__main__':
    unittest.main()
