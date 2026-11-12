"""Clock setup preserves NTP and falls back only when synchronization is absent."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel import clock_sync


class ClockSyncTests(unittest.TestCase):
    def test_synchronized_ntp_performs_no_network_request(self):
        with patch.object(clock_sync.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'yes\n', '')) as run:
            clock_sync.synchronize()
        self.assertEqual(run.call_count, 1)

    def test_unsynchronized_host_uses_https_with_certificate_validation(self):
        with patch.object(clock_sync.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, 'no\n', ''),
                subprocess.CompletedProcess([], 3, '', ''),
                subprocess.CompletedProcess([], 0, '', '')]) as run:
            clock_sync.synchronize()
        arguments=run.call_args.args[0]
        self.assertEqual(arguments[0], 'htpdate')
        self.assertIn('-c', arguments)
        self.assertEqual([arg for arg in arguments if arg.startswith('https://')], list(clock_sync.SOURCES))

    def test_existing_time_daemon_is_not_run_twice(self):
        with patch.object(clock_sync.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, 'no\n', ''),
                subprocess.CompletedProcess([], 0, '', '')]) as run:
            clock_sync.synchronize()
        self.assertEqual(run.call_count, 2)

    def test_install_keeps_active_ntp_and_schedules_sync_without_waiting(self):
        calls=[]
        def execute(*args):
            calls.append(args)
            return subprocess.CompletedProcess([], 0, '', '')
        with patch.object(clock_sync, 'systemctl', side_effect=execute), patch.object(clock_sync.Path, 'is_file', return_value=False):
            clock_sync.configure()
        self.assertIn(('enable', 'chrony.service'), calls)
        self.assertIn(('enable', '--now', 'buggcraft-clock-sync.timer'), calls)
        self.assertIn(('start', '--no-block', 'buggcraft-clock-sync.service'), calls)

    def test_network_failure_is_reported_by_fallback_job(self):
        with patch.object(clock_sync.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, 'no\n', ''),
                subprocess.CompletedProcess([], 3, '', ''),
                subprocess.CalledProcessError(1, ['htpdate'])]):
            with self.assertRaises(subprocess.CalledProcessError):
                clock_sync.synchronize()


if __name__ == '__main__':
    unittest.main()
