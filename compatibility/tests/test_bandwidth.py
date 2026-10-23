"""Host RX/TX sampling, counter discontinuities and sampler lifecycle."""
import asyncio
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tunnel.bandwidth import Bandwidth, interface_counters


class BandwidthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 10.0
        self.counters = {'eth0': (100, 200)}
        self.reader = Mock(side_effect=lambda: dict(self.counters))
        self.meter = Bandwidth(self.reader, lambda: self.now)

    async def test_decimal_rx_tx_rates_use_elapsed_time_and_cache_reads(self):
        self.assertEqual(self.meter.snapshot()['status'], 'warming_up')
        await self.meter.sample()
        self.assertIsNone(self.meter.snapshot()['rx_bytes_per_second'])
        self.now += 2
        self.counters['eth0'] = (2_000_100, 8_000_200)
        await self.meter.sample()
        sample = self.meter.snapshot()
        self.assertEqual(sample['status'], 'ready')
        self.assertEqual(sample['rx_bytes_per_second'], 1_000_000)
        self.assertEqual(sample['tx_bytes_per_second'], 4_000_000)
        self.assertEqual(sample['sample_seconds'], 2)
        self.assertEqual(self.meter.snapshot(), sample)
        self.assertEqual(self.reader.call_count, 2)
        self.assertNotIn('eth0', str(sample))

    async def test_idle_is_zero_but_expired_samples_are_unknown(self):
        await self.meter.sample()
        self.now += 1
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['rx_bytes_per_second'], 0)
        self.now += 6
        sample = self.meter.snapshot()
        self.assertEqual(sample['status'], 'stale')
        self.assertIsNone(sample['rx_bytes_per_second'])
        self.assertIsNone(sample['tx_bytes_per_second'])

    async def test_interface_removal_addition_and_reset_do_not_create_spikes(self):
        self.counters['removed'] = (9_000_000, 9_000_000)
        await self.meter.sample()
        self.now += 1
        self.counters = {'eth0': (1100, 2200), 'new': (90_000_000, 80_000_000)}
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['rx_bytes_per_second'], 1000)
        self.assertEqual(self.meter.snapshot()['tx_bytes_per_second'], 2000)
        self.now += 1
        self.counters = {'eth0': (1, 2), 'new': (90_000_100, 80_000_300)}
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['rx_bytes_per_second'], 100)
        self.assertEqual(self.meter.snapshot()['tx_bytes_per_second'], 300)

    async def test_multiple_interfaces_are_summed(self):
        self.counters['eth1'] = (300, 400)
        await self.meter.sample()
        self.now += 1
        self.counters = {'eth0': (110, 230), 'eth1': (320, 440)}
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['rx_bytes_per_second'], 30)
        self.assertEqual(self.meter.snapshot()['tx_bytes_per_second'], 70)

    async def test_missing_counters_and_read_failures_recover_with_new_baseline(self):
        for failure in ({}, OSError('not permitted')):
            with self.subTest(failure=str(failure)):
                self.reader.side_effect = failure if isinstance(failure, Exception) else lambda: {}
                await self.meter.sample()
                self.assertEqual(self.meter.snapshot()['status'], 'unavailable')
                self.assertIsNone(self.meter.snapshot()['tx_bytes_per_second'])
                self.reader.side_effect = lambda: dict(self.counters)
                await self.meter.sample()
                self.assertEqual(self.meter.snapshot()['status'], 'warming_up')
                self.now += 1
                await self.meter.sample()
                self.assertEqual(self.meter.snapshot()['status'], 'ready')

    async def test_counter_reset_and_same_timestamp_need_a_new_sample(self):
        await self.meter.sample()
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['status'], 'warming_up')
        self.now += 1
        self.counters = {'eth0': (1, 2)}
        await self.meter.sample()
        self.assertIsNone(self.meter.snapshot()['rx_bytes_per_second'])
        self.now += 1
        self.counters = {'eth0': (101, 202)}
        await self.meter.sample()
        self.assertEqual(self.meter.snapshot()['tx_bytes_per_second'], 200)

    async def test_background_sampler_starts_and_is_cancelled_on_cleanup(self):
        self.meter.interval = .01
        context = self.meter.lifecycle(None)
        await anext(context)
        self.assertEqual(self.reader.call_count, 1)
        async with asyncio.timeout(2):
            while self.reader.call_count < 2:
                await asyncio.sleep(.01)
        await context.aclose()
        self.assertTrue(self.meter.task.cancelled())
        count = self.reader.call_count
        await asyncio.sleep(.03)
        self.assertEqual(self.reader.call_count, count)

    def test_os_reader_excludes_loopback_and_returns_only_byte_counters(self):
        def address(ip):
            return SimpleNamespace(family=socket.AF_INET6 if ':' in ip else socket.AF_INET, address=ip)
        def counter(rx, tx):
            return SimpleNamespace(bytes_recv=rx, bytes_sent=tx)
        with patch('tunnel.bandwidth.psutil.net_if_addrs', return_value={
            'lo': [address('127.0.0.1')], 'Windows loopback': [address('::1')],
            'eth0': [address('192.0.2.1')], 'eth1': [address('fe80::1%3')]}), \
                patch('tunnel.bandwidth.psutil.net_io_counters', return_value={
                    'lo':counter(999, 999), 'Windows loopback':counter(999, 999),
                    'eth0':counter(10, 20), 'eth1':counter(30, 40)}) as read:
            self.assertEqual(interface_counters(), {'eth0':(10, 20), 'eth1':(30, 40)})
            read.assert_called_once_with(pernic=True, nowrap=False)
