"""Signed membership, replay expiry and ambiguous room ownership."""
import base64
import copy
from pathlib import Path
import secrets
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from tunnel.federation import Federation, TTL, canonical
from tunnel.relay import RelayServer


class FederationIntegrityTests(unittest.TestCase):
    def setUp(self):
        network = secrets.token_urlsafe(48)
        self.nodes = []
        for name in ('A', 'B', 'C'):
            key = asyncssh.generate_private_key('ssh-ed25519')
            profile = dict(server_host=name.lower() + '.example', server_port=3334,
                           server_public_key=key.export_public_key().decode(), token=secrets.token_urlsafe(48))
            fed = Federation(key, profile, network_key=network, name=name)
            fed.relay = RelayServer(host_key=key, token=profile['token'])
            fed.publish()
            self.nodes.append(fed)
        self.a, self.b, self.c = self.nodes

    def signed_record(self, fed, **changes):
        record = copy.deepcopy(fed.records[fed.id])
        record['body'].update(changes)
        record['signature'] = base64.b64encode(fed.key.sign(canonical(record['body']), b'ssh-ed25519')).decode()
        return record

    def test_altered_signature_network_proof_and_identity_are_rejected(self):
        original = self.b.records[self.b.id]
        for change in ({'name': 'Forged'}, {'id': self.c.id}):
            record = copy.deepcopy(original)
            record['body'].update(change)
            self.a.ingest([record])
            self.assertNotIn(self.b.id, self.a.records)
        record = copy.deepcopy(original)
        record['proof'] = '0' * 64
        self.a.ingest([record])
        self.assertNotIn(self.b.id, self.a.records)
        self.a.ingest([original])
        self.assertIn(self.b.id, self.a.live())

    def test_replaying_same_announcement_cannot_extend_lease(self):
        record = self.b.records[self.b.id]
        self.a.ingest([record])
        seen = self.a.seen[self.b.id]
        with patch('tunnel.federation.time.monotonic', return_value=seen + TTL + 1):
            self.a.ingest([copy.deepcopy(record)])
            self.assertEqual(self.a.seen[self.b.id], seen)
            self.assertNotIn(self.b.id, self.a.live())

    def test_expired_future_and_oversized_signed_announcements_are_ignored(self):
        for changes in ({'issued': time.time() - TTL - 1}, {'issued': time.time() + 60},
                        {'rooms': [{'id': secrets.token_hex(16)} for _ in range(129)]}):
            with self.subTest(changes=tuple(changes)):
                self.a.ingest([self.signed_record(self.b, **changes)])
                self.assertNotIn(self.b.id, self.a.records)

    def test_multiple_signed_room_owners_do_not_select_arbitrary_node(self):
        room = secrets.token_hex(16)
        self.a.ingest([self.signed_record(self.b, rooms=[{'id': room}])])
        self.assertEqual(self.a.locate(room), self.b.profile)
        self.a.ingest([self.signed_record(self.c, rooms=[{'id': room}])])
        self.assertIsNone(self.a.locate(room))

    def test_local_block_refuses_membership_without_mutating_other_nodes(self):
        self.a.blocked.add(self.b.id)
        record = self.b.records[self.b.id]
        self.a.ingest([record])
        self.c.ingest([record])
        self.assertNotIn(self.b.id, self.a.live())
        self.assertFalse(self.a.accepts(self.b.id, self.b.key))
        self.assertIn(self.b.id, self.c.live())
        self.assertTrue(self.c.accepts(self.b.id, self.b.key))
