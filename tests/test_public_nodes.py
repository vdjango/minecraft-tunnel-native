"""Public listings omit offline members while administrators retain their history."""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.portal import Portal


class PublicNodesTests(unittest.TestCase):
    def test_public_status_excludes_offline_suspect_and_blocked_seeds(self):
        peers=[dict(id=status,status=status,name=status) for status in
               ('online','indirect','offline','suspect','blocked')]
        federation=Mock(name='node',id='local',seeds={})
        federation.name='node'
        federation.live.return_value={'online':{},'indirect':{}}
        federation.health.return_value=peers
        federation.online_players.return_value=0
        federation.key.get_fingerprint.return_value='SHA256:fixture'
        relay=SimpleNamespace(rooms={},peers=[],streams={},draining=False,
                              max_rooms=32,max_connections=512,capacity=Mock())
        relay.capacity.snapshot.return_value={}
        diagnostics=Mock()
        diagnostics.transport_counts.return_value=dict(p2p=0,relay=0,unknown=0)
        now=time.monotonic()
        portal=SimpleNamespace(federation=federation,relay=relay,diagnostics=diagnostics,
            state={},started=now,last_uptime_checkpoint=now,total_uptime_seconds=0,
            account_api=SimpleNamespace(mail=SimpleNamespace(required=False)))
        result=Portal.public_state(portal)
        self.assertEqual([node['status'] for node in result['nodes']],['online','indirect'])
        self.assertEqual(len(federation.health()),5)
