"""Real SSH gossip tests. Run without the launcher: python -m unittest discover -s tests."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from tunnel.accounts import Accounts, canonical, encode
from tunnel.account_replication import header
from tunnel.federation import Federation, TTL
from tunnel.relay import RelayServer


def sign(key, value):
    r, s = utils.decode_dss_signature(key.sign(value if isinstance(value, bytes) else canonical(value), ec.ECDSA(hashes.SHA256())))
    return encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))


def public(key):
    n = key.public_key().public_numbers()
    return dict(kty='EC', crv='P-256', x=encode(n.x.to_bytes(32, 'big')), y=encode(n.y.to_bytes(32, 'big')))


class ReplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feds, self.relays, self.stores = [], [], []
        self.network = secrets.token_urlsafe(48)
        for index in range(4):
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48)).start()
            profile = dict(server_host='127.0.0.1', server_port=relay.port, token=relay.token,
                           server_public_key=key.export_public_key().decode())
            fed = Federation(key, profile, network_key=self.network, name='Node ' + str(index))
            fed.relay, relay.federation = relay, fed
            folder = Path(self.temp.name) / str(index)
            folder.mkdir()
            store = Accounts(fed, folder / 'accounts-replica.json')
            fed.accounts = store
            relay.account_authenticate = store.authenticate
            fed.publish()
            self.feds.append(fed); self.relays.append(relay); self.stores.append(store)
        self.active = set(range(4))
        for fed in self.feds[1:]:
            fed.join(self.feds[0].invitation())
        await self.rounds(3)

    async def asyncTearDown(self):
        await asyncio.gather(*(f.close() for f in self.feds))
        await asyncio.gather(*(r.close() for r in self.relays))

    async def rounds(self, count=5):
        for _ in range(count):
            for index in sorted(self.active):
                await self.feds[index].sync_once()

    def account(self, copies=1, home=0, username='player'):
        key = ec.generate_private_key(ec.SECP256R1())
        uid = secrets.token_hex(16)
        state = dict(account=uid + '.' + self.feds[home].id, revision=1,
                     box=dict(salt=encode(secrets.token_bytes(16)), iv=encode(secrets.token_bytes(12)),
                              ciphertext=encode(secrets.token_bytes(64)), iterations=600000))
        routing = dict(account=state['account'], revision=1, copies=copies,
                       digest=hashlib.sha256(canonical(state)).hexdigest())
        record = self.stores[home].enroll(uid, username, public(key), state, sign(key, state), copies, sign(key, routing))
        return record, key

    async def stop(self, index):
        self.active.remove(index)
        await self.relays[index].close()
        for n in self.active:
            self.feds[n].seen[self.feds[index].id] -= TTL + 1

    async def test_zero_backups_publish_name_without_durable_vault(self):
        record, _ = self.account(0)
        await self.rounds()
        account = record['state']['account']
        self.assertIn(account, json.dumps(self.feds[0].snapshot()))
        for store in self.stores[1:]:
            self.assertNotIn(account, store.records)
            self.assertIn(account, store.replication.directory)
            self.assertEqual(await store.replication.resolve('PLAYER'), record)
        await self.stop(0)
        await self.rounds()
        self.assertFalse(any(account in self.stores[i].records for i in self.active))

    async def test_requested_copy_count_and_directory_has_no_ciphertext(self):
        record, key = self.account(1)
        await self.rounds()
        account = record['state']['account']
        self.assertEqual(sum(account in s.records for s in self.stores), 2)
        for store in self.stores:
            self.assertIn(account, store.replication.directory)
            self.assertNotIn('ciphertext', json.dumps(store.replication.snapshot()))
            self.assertEqual(store.replication.status(account)['available'], 2)

    async def test_non_replica_username_login_fetches_only_temporary_copy(self):
        record, key = self.account(1)
        await self.rounds()
        account = record['state']['account']
        store = next(s for s in self.stores if account not in s.records)
        resolved = await store.replication.resolve('PLAYER')
        self.assertEqual(resolved, record)
        self.assertNotIn(account, store.records)
        self.assertNotIn('ciphertext', store.replication.path.read_text())
        challenge = self.stores[0].challenge('login', account, 'https://trusted.example')
        self.stores[0].authorize(account, challenge, sign(key, challenge['message'].encode()))
        token = self.stores[0].credential(account)
        self.assertEqual(store.authenticate(token), account)

    async def test_home_failure_replenishes_copy_without_leader(self):
        record, _ = self.account(1)
        await self.rounds()
        account = record['state']['account']
        await self.stop(0)
        await self.rounds()
        self.assertEqual(sum(account in self.stores[i].records for i in self.active), 2)
        for i in self.active:
            self.assertEqual(await self.stores[i].replication.resolve(account), record)

    async def test_backup_failure_replenishes_and_keeps_home(self):
        record, _ = self.account(1)
        await self.rounds()
        account = record['state']['account']
        backup = next(i for i in range(1, 4) if account in self.stores[i].records)
        await self.stop(backup)
        await self.rounds()
        self.assertIn(account, self.stores[0].records)
        self.assertEqual(sum(account in self.stores[i].records for i in self.active), 2)

    async def test_tampered_policy_cannot_increase_replication(self):
        record, _ = self.account(1)
        altered = copy.deepcopy(record)
        altered['routing']['copies'] = 8
        with self.assertRaises(ValueError):
            self.stores[1].replication.accept(header(altered))
        self.stores[1].ingest([dict(record=altered, proof=self.feds[0].proof(self.feds[0].id))])
        self.assertNotIn(record['state']['account'], self.stores[1].records)

    async def test_backup_revocation_preserves_routed_login(self):
        record, key = self.account(1)
        await self.rounds()
        account = record['state']['account']
        newer = copy.deepcopy(record)
        newer['state']['revision'] = 2
        newer['signature'] = sign(key, newer['state'])
        newer['routing'].update(revision=2, copies=0, digest=hashlib.sha256(canonical(newer['state'])).hexdigest())
        newer['routing_signature'] = sign(key, newer['routing'])
        self.stores[0].ingest_one(newer); self.stores[0].save()
        await self.rounds()
        for store in self.stores[1:]:
            self.assertNotIn(account, store.records)
            self.assertEqual(await store.replication.resolve('player'), newer)
        # An old signed opt-in cannot override the signed revocation.
        for store in self.stores[1:]:
            store.ingest([dict(record=record, proof=self.feds[0].proof(self.feds[0].id))])
            self.assertNotIn(account, store.records)

    async def test_network_directory_rejects_duplicate_username(self):
        first, _ = self.account(1, username='same')
        await self.rounds()
        with self.assertRaises(ValueError):
            self.account(1, home=1, username='SAME')
        self.assertEqual(await self.stores[2].replication.resolve(first['state']['account']), first)

    async def test_shortage_reports_actual_count_and_preserves_last_copy(self):
        record, _ = self.account(8)
        await self.rounds()
        account = record['state']['account']
        self.assertEqual(self.stores[0].replication.status(account)['available'], 4)
        self.assertEqual(self.stores[0].replication.status(account)['target'], 9)
        await self.stop(0); await self.stop(1); await self.stop(2)
        await self.rounds(2)
        self.assertEqual(await self.stores[3].replication.resolve(account), record)
        self.assertIn(account, self.stores[3].records)

    async def test_persistent_directory_restarts_without_vault(self):
        record, _ = self.account(1)
        await self.rounds()
        account = record['state']['account']
        store = next(s for s in self.stores if account not in s.records)
        restored = Accounts(store.federation, store.path)
        self.assertNotIn(account, restored.records)
        self.assertIn(account, restored.replication.directory)
        self.assertEqual(await restored.replication.resolve(account), record)


if __name__ == '__main__':
    unittest.main()
