"""Run the original protocol contracts against a real C++ core, not a mock."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'compatibility'), str(ROOT / 'web')]
from tunnel.native_relay import RelayServer
import tunnel.relay
tunnel.relay.RelayServer = RelayServer

MODULES = ['test_relay_security', 'test_capacity', 'test_peer_wire',
           'test_peer_signaling_stun', 'test_heartbeat', 'test_account_replication',
           'test_federation_integrity', 'test_player_diagnostics']
if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ['tests.' + name for name in (sys.argv[1:] or MODULES)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
