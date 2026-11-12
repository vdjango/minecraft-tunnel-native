"""Admission estimates use upload Mbps converted to decimal MB/s."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.capacity import Capacity


class CapacityBudgetTests(unittest.TestCase):
    def test_provider_upload_rates_match_the_displayed_visitor_budget(self):
        capacity=Capacity(SimpleNamespace(max_rooms=128,peers=set()))
        for mbps,expected in ((6,3),(20,11),(200,110)):
            with self.subTest(upload_mbps=mbps):
                capacity.configure(dict(bandwidth_mb_s=mbps/8))
                self.assertEqual(capacity.limit(),expected)

    def test_unknown_upload_capacity_has_no_invented_headcount(self):
        capacity=Capacity(SimpleNamespace(max_rooms=128,peers=set()))
        capacity.configure(dict(bandwidth_mb_s=0))
        self.assertIsNone(capacity.snapshot()['remaining_visitors'])
