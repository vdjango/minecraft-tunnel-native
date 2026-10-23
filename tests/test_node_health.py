"""Public route hints are bounded, cached, signed and reveal no OS counters."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from tunnel.node_health import snapshot, validate


class NodeHealthTests(unittest.TestCase):
    def setUp(self):
        self.capacity=Mock(policy=dict(bandwidth_mb_s=10))
        self.capacity.snapshot.return_value=dict(bandwidth_mb_s=10,reserve_percent=20,
            payload_mb_s=1,remaining_visitors=20)
        self.fed=SimpleNamespace(region='Test region',relay=SimpleNamespace(capacity=self.capacity,
            rooms={},peers=set(),draining=False,max_rooms=128),bandwidth=Mock())
        self.fed.bandwidth.snapshot.return_value=dict(status='ready',tx_bytes_per_second=6_000_000)

    def test_real_outgoing_load_counts_and_counters_stay_private(self):
        sample=snapshot(self.fed)
        self.assertEqual(sample['utilization'],.75)
        self.assertEqual(sample['region'],'Test region')
        self.assertEqual(set(sample),{'region','accepting','utilization','remaining_visitors'})

    def test_probes_share_cached_snapshot_and_policy_changes_invalidate_it(self):
        with patch('tunnel.node_health.time.monotonic',return_value=10):
            first=snapshot(self.fed)
            first['accepting']=False
            self.assertTrue(snapshot(self.fed)['accepting'])
            self.capacity.snapshot.assert_called_once()
            self.fed.relay.draining=True
            self.assertFalse(snapshot(self.fed)['accepting'])
            self.assertEqual(self.capacity.snapshot.call_count,2)

    def test_stale_os_measurement_falls_back_to_payload_load(self):
        self.fed.bandwidth.snapshot.return_value=dict(status='stale',tx_bytes_per_second=6_000_000)
        self.assertEqual(snapshot(self.fed)['utilization'],.14)

    def test_unknown_bandwidth_is_unknown_and_not_zero(self):
        self.capacity.snapshot.return_value['bandwidth_mb_s']=0
        self.assertIsNone(snapshot(self.fed)['utilization'])

    def test_invalid_signed_route_metadata_is_rejected(self):
        for hint in [dict(utilization=float('nan')),dict(utilization=1.1),dict(utilization=True),
                     dict(remaining_visitors=-1),dict(remaining_visitors=.5),dict(accepting='yes'),dict(region='x'*81)]:
            with self.subTest(hint=hint),self.assertRaises(ValueError):
                validate(hint)
        self.assertEqual(validate(dict(region='',utilization=None,remaining_visitors=None,accepting=True))['accepting'],True)
